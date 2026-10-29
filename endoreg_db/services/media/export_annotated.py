# endoreg_db/services/media/export_annotated.py

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from collections.abc import Mapping
from pathlib import Path

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied, ValidationError

from lx_dtypes.models.contracts.export_annotated import ExportAnnotatedConfigContract
from lx_dtypes.models.contracts.json_types import JsonValue
from lx_dtypes.models.contracts.video_frame_export import export_config, export_result
from endoreg_db.export.frames.export_frames_with_labels import (
    AnnotationExporterClient,
    ExportJobFailedError,
    validate_annotation_table_destination,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hub.deployment import local_study_server_mode_enabled
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.annotations.segment_validation_workflow import (
    SEGMENT_ANNOTATION_FINAL_STATUSES,
    SegmentAnnotationStatus,
    resolve_segment_annotation_status,
)
from endoreg_db.utils.paths import (
    ensure_within_storage_root,
    get_runtime_paths,
    normalize_protected_media_relative_path,
)

logger = logging.getLogger(__name__)


class ExportConflictError(RuntimeError):
    pass


def confined_api_export_location(
    contract: ExportAnnotatedConfigContract,
) -> tuple[Path, Path]:
    """Resolve browser-supplied export paths beneath the protected export root."""
    return confine_export_location(contract.output_dir, contract.output_path)


def confine_export_location(
    output_dir: str | Path, output_path: str | Path
) -> tuple[Path, Path]:
    """Return the absolute export directory and relative table path, or raise."""
    try:
        relative_dir = normalize_protected_media_relative_path(output_dir)
        relative_file = normalize_protected_media_relative_path(output_path)
    except ValueError as exc:
        raise ValidationError(f"Invalid export location: {exc}") from exc

    export_root = get_runtime_paths().frame_annotation_export.resolve()
    try:
        output_dir = ensure_within_storage_root(export_root / relative_dir)
    except ValueError as exc:
        raise ValidationError(f"Invalid export location: {exc}") from exc
    if not output_dir.is_relative_to(export_root):
        raise ValidationError(
            f"Export directory must stay within the annotation export root: {relative_dir}"
        )
    # Resolving the full destination rejects existing nested symlinks that escape.
    table_path = (output_dir / relative_file).resolve()
    if not table_path.is_relative_to(export_root):
        raise ValidationError(
            f"Export table must stay within the annotation export root: {relative_file}"
        )
    try:
        validate_annotation_table_destination(output_dir / relative_file)
    except ValueError as exc:
        raise ValidationError(f"Invalid export location: {exc}") from exc
    lock_root = (export_root / ".locks").resolve()
    if output_dir.is_relative_to(lock_root) or table_path.is_relative_to(lock_root):
        raise ValidationError(
            "Invalid export location: .locks is reserved for job locks."
        )
    return output_dir, Path(relative_file)


@dataclass(slots=True)
class ExportAnnotatedService:
    client: AnnotationExporterClient

    @classmethod
    def default(cls) -> ExportAnnotatedService:
        return cls(client=AnnotationExporterClient())

    def run_api_export(
        self,
        *,
        payload: Mapping[str, JsonValue],
        user: User | AnonymousUser,
    ) -> export_result:
        contract = ExportAnnotatedConfigContract.from_api_payload(dict(payload))
        output_dir, output_path = confined_api_export_location(contract)

        video = VideoFile.objects.select_related("state", "center").get(
            pk=contract.video_id
        )
        self._validate_scope_after_loading(config=contract, user=user, video=video)
        self._validate_video_specific_export(video)

        config: export_config = replace(
            contract.to_export_config(),
            output_dir=output_dir,
            output_path=output_path,
        )
        try:
            return self.client.run_export(config)
        except ExportJobFailedError:
            raise
        except Exception as exc:
            logger.exception("Annotated export failed")
            raise ExportJobFailedError(
                "annotation export failed",
                original_error=exc,
            ) from exc

    def _validate_scope_after_loading(
        self,
        *,
        config: ExportAnnotatedConfigContract,
        user: User | AnonymousUser,
        video: VideoFile,
    ) -> None:
        if config.center_key:
            if not Center.objects.filter(center_key=config.center_key).exists():
                raise ValidationError(f"Unknown center_key: {config.center_key}")

        if config.all_centers and not getattr(user, "is_staff", False):
            raise PermissionDenied("all_centers export is only allowed for staff users")

        allowed_center_ids = resolve_allowed_center_ids(user)
        if allowed_center_ids is not None and video.center_id not in allowed_center_ids:
            raise PermissionDenied("video is outside the authenticated center scope")

        if config.center_key and config.center_key != video.center.center_key:
            raise PermissionDenied("center_key does not match the requested video")

        if not local_study_server_mode_enabled():
            return

        if not (bool(config.center_key) ^ bool(config.all_centers)):
            raise PermissionDenied(
                "local_study_server exports require exactly one center scope: "
                "center_key or all_centers"
            )

    def _validate_video_specific_export(
        self,
        video: VideoFile,
    ) -> None:
        status = resolve_segment_annotation_status(video)
        if (
            status != SegmentAnnotationStatus.NOT_STARTED.value
            and status not in SEGMENT_ANNOTATION_FINAL_STATUSES
        ):
            raise ExportConflictError(
                f"cleanup_required: segment annotations are not finalized ({status})"
            )
