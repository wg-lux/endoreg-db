from __future__ import annotations

from contextlib import nullcontext
from functools import partial
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
import logging

from cryptography.exceptions import InvalidTag
from django.apps import apps
from django.db import models
from django.db.models.fields.files import FieldFile

from endoreg_db.config.secret_keyring import configured_master_keyring
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.media_operation_gate import (
    MediaOperationDeferred,
    video_artifact_mutation,
    video_artifact_publication,
)
from endoreg_db.utils.encryption.encrypted import EncryptedStorage, LazyEncryptedStorage
from endoreg_db.utils.rust_backend import is_lx_encrypted_file
from endoreg_db.utils.encryption.rotation import rotate_encrypted_file
from endoreg_db.utils.paths import protected_media_root
from endoreg_db.utils.structured_logging import emit_structured_event, path_reference

logger = logging.getLogger(__name__)


@dataclass
class StorageRotationReport:
    scanned: int = 0
    rotated: int = 0
    authenticated: int = 0
    deferred: int = 0
    failed: int = 0
    unmanaged_encrypted: int = 0
    outside_application_encryption: int = 0
    hls_regenerated: int = 0
    hls_pending: int = 0
    historical_hls_keys_pending_retirement: int = 0
    staging_files_pending_review: int = 0
    missing_registered_files: int = 0

    def counts(self) -> dict[str, int]:
        return cast(dict[str, int], asdict(self))


def _registered_files(root: Path) -> dict[Path, set[int]]:
    """Resolve registered FileFields through their actual storage backend."""
    registered: dict[Path, set[int]] = {}
    for model in apps.get_app_config("endoreg_db").get_models():
        fields = [
            field
            for field in model._meta.get_fields()
            if isinstance(field, models.FileField)
        ]
        for field in fields:
            if not isinstance(field.storage, (EncryptedStorage, LazyEncryptedStorage)):
                continue
            for row in model._default_manager.exclude(**{field.name: ""}).iterator():
                file = getattr(row, field.name)
                if not isinstance(file, FieldFile) or not file.name:
                    continue
                path = Path(file.path).absolute()
                if not path.is_relative_to(root):
                    raise ValueError(
                        "Registered encrypted artifact lies outside the protected storage root"
                    )
                owners = registered.setdefault(path, set())
                if isinstance(row, VideoFile):
                    owners.add(int(row.pk))
                else:
                    video_id: object = getattr(row, "video_id", None)
                    if isinstance(video_id, int):
                        owners.add(video_id)
    return registered


def rotate_storage(
    *, apply: bool = False, include_hls: bool = False
) -> StorageRotationReport:
    ring = configured_master_keyring()
    if ring is None:
        raise ValueError("Online rotation requires a private master-key manifest")
    root = protected_media_root().absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(
            "Protected storage root must be an existing directory without links"
        )
    registered = _registered_files(root)
    report = StorageRotationReport()
    report.missing_registered_files = sum(not path.is_file() for path in registered)

    def check_generation() -> None:
        if configured_master_keyring() != ring:
            raise ValueError(
                "Keyring changed during rotation; rerun with the intended generations"
            )

    for path in root.rglob("*"):
        if path.name.startswith(".lx-rotation-"):
            if path.suffix != ".lock":
                report.staging_files_pending_review += 1
            continue
        if path.is_symlink():
            report.failed += 1
            continue
        if not path.is_file():
            continue
        report.scanned += 1
        try:
            encrypted = is_lx_encrypted_file(path)
            if not encrypted:
                report.outside_application_encryption += 1
                continue
            owners = registered.get(path)
            if owners is None or len(owners) > 1:
                report.unmanaged_encrypted += 1
                continue
            owner = next(iter(owners), None)
            lease = (
                video_artifact_mutation(video_id=owner)
                if apply and owner is not None
                else nullcontext()
            )
            with lease:
                expected_digest: str | None = None
                if owner is not None:
                    video = VideoFile.objects.get(pk=owner)
                    if video.raw_file and Path(video.raw_file.path).absolute() == path:
                        expected_digest = video.raw_video_hash or None
                    elif (
                        video.processed_file
                        and Path(video.processed_file.path).absolute() == path
                    ):
                        expected_digest = video.processed_video_hash or None
                    else:
                        raise ValueError(
                            "Video no longer owns the inventoried ciphertext"
                        )
                result = rotate_encrypted_file(
                    path,
                    active_key=ring.active,
                    retiring_keys=ring.retiring,
                    before_publish=check_generation,
                    publication_guard=partial(
                        video_artifact_publication, video_id=owner
                    )
                    if owner is not None
                    else None,
                    expected_plaintext_sha256=expected_digest,
                    apply=apply,
                )
            report.rotated += int(result.changed)
            report.authenticated += 1
        except MediaOperationDeferred:
            report.deferred += 1
            emit_structured_event(
                logger, "storage_rotation_deferred", artifact=path_reference(path)
            )
        except (OSError, ValueError, InvalidTag) as exc:
            report.failed += 1
            emit_structured_event(
                logger,
                "storage_rotation_failed",
                level=logging.ERROR,
                artifact=path_reference(path),
                error_type=type(exc).__name__,
            )
    if include_hls:
        _rotate_hls(report, apply=apply)
    return report


def _rotate_hls(report: StorageRotationReport, *, apply: bool) -> None:
    from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
    from endoreg_db.services.hls_media import (
        hls_uses_active_master_key,
        materialize_video_hls,
    )

    for artifact in VideoHlsArtifact.objects.exclude(
        key_ciphertext__isnull=True
    ).iterator():
        try:
            if hls_uses_active_master_key(artifact):
                continue
            if artifact.status != VideoHlsArtifact.Status.READY:
                report.historical_hls_keys_pending_retirement += 1
                continue
            if not apply:
                report.hls_pending += 1
                continue
            # This creates fresh content keys and a matched segment generation,
            # respecting the existing source-quality gates and playback leases.
            result = materialize_video_hls(
                int(artifact.video_id), artifact_kind=artifact.artifact_kind, force=True
            )
            if result.status == "already_materializing":
                report.deferred += 1
            elif result.status not in {"materialized", "already_ready"}:
                report.failed += 1
            else:
                ready = VideoHlsArtifact.objects.get(key_id=result.key_id)
                if not hls_uses_active_master_key(ready):
                    raise ValueError(
                        "Published streaming generation still uses a retiring key"
                    )
                report.hls_regenerated += 1
        except MediaOperationDeferred:
            report.deferred += 1
        except (OSError, ValueError, InvalidTag) as exc:
            report.failed += 1
            emit_structured_event(
                logger,
                "hls_rotation_failed",
                level=logging.ERROR,
                video_id=int(artifact.video_id),
                artifact_id=int(artifact.pk),
                error_type=type(exc).__name__,
            )
