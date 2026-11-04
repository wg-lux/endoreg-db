from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone

from endoreg_db.import_files.context.file_lock import content_hash_lock
from endoreg_db.models import Center, VideoFile, VideoState
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.upload_job_file import UploadJobFile
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.services.hub.upload_job_files import register_upload_job_file
from endoreg_db.services.runtime.transient_artifact_cleanup import (
    TransientArtifactDecision,
    TransientArtifactOwnerKind,
    classify_transient_artifact,
    reap_transient_artifact,
    run_transient_artifact_cleanup,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
)
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.django_db

GRACE_SECONDS = 6 * 60 * 60
STALE_AGE_SECONDS = 2 * 24 * 60 * 60


@pytest.fixture
def created_paths() -> Generator[list[Path]]:
    paths: list[Path] = []
    yield paths
    for path in paths:
        if path.is_dir():
            safe_rmtree(path, missing_ok=True)
        else:
            safe_unlink_file(path, missing_ok=True)


def _backdate(path: Path, age_seconds: float) -> None:
    stamp = time.time() - age_seconds
    for root, directory_names, file_names in os.walk(path):
        for name in (*directory_names, *file_names):
            os.utime(Path(root) / name, (stamp, stamp))
    os.utime(path, (stamp, stamp))


def _write(path: Path, *, age_seconds: float = STALE_AGE_SECONDS) -> Path:
    atomic_write_file(destination=path, content=[b"attempt-output"])
    _backdate(path, age_seconds)
    return path


def _video(*, raw_hash: str | None = None) -> VideoFile:
    return VideoFile.objects.create(
        center=Center.objects.create(name=f"transient-{uuid4().hex}"),
        raw_video_hash=raw_hash or hashlib.sha256(uuid4().bytes).hexdigest(),
        state=VideoState.objects.create(),
    )


def _classify(unit: Path, kind: TransientArtifactOwnerKind):
    return classify_transient_artifact(
        unit, kind, grace_seconds=GRACE_SECONDS, now=time.time()
    )


def _hls_unit(created_paths: list[Path], video: VideoFile, key_id: str) -> Path:
    unit = get_runtime_paths().transcoding / "hls_output" / str(video.pk) / key_id
    _write(unit / "segment.ts")
    _backdate(unit, STALE_AGE_SECONDS)
    created_paths.append(unit.parent)
    return unit


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (VideoHlsArtifact.Status.MATERIALIZING, TransientArtifactDecision.OWNER_ACTIVE),
        (VideoHlsArtifact.Status.QUEUED, TransientArtifactDecision.OWNER_ACTIVE),
        (VideoHlsArtifact.Status.FAILED, TransientArtifactDecision.DELETE),
        (VideoHlsArtifact.Status.READY, TransientArtifactDecision.DELETE),
        (None, TransientArtifactDecision.UNPROVEN_OWNER),
    ],
)
def test_hls_build_directory_follows_its_artifact_attempt(
    created_paths: list[Path],
    status: VideoHlsArtifact.Status | None,
    expected: TransientArtifactDecision,
) -> None:
    video = _video()
    key_id = uuid4()
    if status is not None:
        VideoHlsArtifact.objects.create(
            video=video,
            artifact_kind="processed",
            status=status,
            key_id=key_id,
            error_code=(
                VideoHlsArtifact.ErrorCode.MATERIALIZATION_FAILED
                if status == VideoHlsArtifact.Status.FAILED
                else VideoHlsArtifact.ErrorCode.NONE
            ),
        )
    unit = _hls_unit(created_paths, video, str(key_id))

    item = _classify(unit, TransientArtifactOwnerKind.HLS_BUILD)

    assert item.owner_kind is TransientArtifactOwnerKind.HLS_BUILD
    assert item.decision is expected


def test_active_media_lease_blocks_terminal_hls_directory(
    created_paths: list[Path],
) -> None:
    video = _video()
    key_id = uuid4()
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind="processed",
        status=VideoHlsArtifact.Status.READY,
        key_id=key_id,
    )
    unit = _hls_unit(created_paths, video, str(key_id))
    MediaOperationLease.objects.create(
        video=video,
        lease_type=MediaOperationLease.LEASE_ARTIFACT_WRITE,
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    item = _classify(unit, TransientArtifactOwnerKind.HLS_BUILD)

    assert item.decision is TransientArtifactDecision.OWNER_ACTIVE


@pytest.mark.parametrize("processing_status", ["running", "pending", "success", None])
def test_raw_hash_artifact_follows_video_processing(
    created_paths: list[Path], processing_status: str | None
) -> None:
    video = _video()
    if processing_status is not None:
        VideoProcessingHistory.objects.create(
            video=video, operation="test_transient_cleanup", status=processing_status
        )
    unit = _write(
        get_runtime_paths().transcoding
        / f"{video.raw_video_hash}.outside_frame_blackening.{uuid4().hex}.mp4"
    )
    created_paths.append(unit)

    item = _classify(unit, TransientArtifactOwnerKind.VIDEO_RAW_HASH)

    expected = (
        TransientArtifactDecision.OWNER_ACTIVE
        if processing_status in {"running", "pending"}
        else TransientArtifactDecision.DELETE
    )
    assert item.decision is expected


def test_held_content_hash_lock_marks_raw_hash_owner_active(
    created_paths: list[Path],
) -> None:
    video = _video()
    unit = _write(
        get_runtime_paths().transcoding / f"{video.raw_video_hash}.attempt.mp4"
    )
    created_paths.append(unit)

    with content_hash_lock(video.raw_video_hash, timeout_seconds=0):
        held = _classify(unit, TransientArtifactOwnerKind.VIDEO_RAW_HASH)
    released = _classify(unit, TransientArtifactOwnerKind.VIDEO_RAW_HASH)

    assert held.decision is TransientArtifactDecision.OWNER_ACTIVE
    assert released.decision is TransientArtifactDecision.DELETE


def test_raw_hash_artifact_of_active_upload_without_video_is_kept(
    created_paths: list[Path],
) -> None:
    raw_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    UploadJob.objects.create(
        file=ContentFile(b"source", name="source.mp4"),
        content_type="video/mp4",
        content_hash=raw_hash,
        status=UploadJob.Status.PROCESSING,
    )
    unit = _write(get_runtime_paths().transcoding / f"{raw_hash}.mp4.part")
    created_paths.append(unit)

    item = _classify(unit, TransientArtifactOwnerKind.VIDEO_RAW_HASH)

    assert item.decision is TransientArtifactDecision.OWNER_ACTIVE


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (UploadJob.Status.PROCESSING, TransientArtifactDecision.OWNER_ACTIVE),
        (UploadJob.Status.ANONYMIZED, TransientArtifactDecision.DELETE),
        (UploadJob.Status.ERROR, TransientArtifactDecision.DELETE),
    ],
)
def test_inventory_registered_workspace_follows_upload_job(
    created_paths: list[Path],
    status: UploadJob.Status,
    expected: TransientArtifactDecision,
) -> None:
    job = UploadJob.objects.create(
        file=ContentFile(b"source", name="source.pdf"),
        content_type="application/pdf",
    )
    workspace = ensure_directory(get_runtime_paths().transcoding / uuid4().hex)
    created_paths.append(workspace)
    register_upload_job_file(
        job.pk, workspace, role=UploadJobFile.Role.WORKING, is_directory=True
    )
    _write(workspace / "copy.pdf")
    _backdate(workspace, STALE_AGE_SECONDS)
    UploadJob.objects.filter(pk=job.pk).update(
        status=status,
        error_code=(
            UploadJob.ErrorCode.PROCESSING_FAILED
            if status == UploadJob.Status.ERROR
            else UploadJob.ErrorCode.NONE
        ),
    )

    item = _classify(workspace, TransientArtifactOwnerKind.PROCESS_SCOPED)

    assert item.owner_kind is TransientArtifactOwnerKind.UPLOAD_JOB_INVENTORY
    assert item.decision is expected


def test_retained_inventory_role_is_an_owner_conflict(
    created_paths: list[Path],
) -> None:
    job = UploadJob.objects.create(
        file=ContentFile(b"source", name="source.pdf"),
        content_type="application/pdf",
        status=UploadJob.Status.ANONYMIZED,
    )
    unit = _write(get_runtime_paths().transcoding / f"{uuid4().hex}-retained.pdf")
    created_paths.append(unit)
    register_upload_job_file(job.pk, unit, role=UploadJobFile.Role.RETAINED)

    item = _classify(unit, TransientArtifactOwnerKind.UNKNOWN)

    assert item.decision is TransientArtifactDecision.OWNER_CONFLICT


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        (
            f"endoreg-fieldfile-{uuid4().hex}.mp4",
            TransientArtifactOwnerKind.PROCESS_SCOPED,
        ),
        (f"upload-source-{uuid4().hex}.mp4", TransientArtifactOwnerKind.PROCESS_SCOPED),
        (f"{uuid4().hex}.pdf", TransientArtifactOwnerKind.PROCESS_SCOPED),
        (f"operator-copy-{uuid4().hex}.bin", TransientArtifactOwnerKind.UNKNOWN),
    ],
)
def test_entries_without_persisted_owner_are_never_deleted(
    created_paths: list[Path], name: str, kind: TransientArtifactOwnerKind
) -> None:
    unit = _write(get_runtime_paths().transcoding / name)
    created_paths.append(unit)

    item = _classify(unit, kind)

    assert item.decision is TransientArtifactDecision.UNPROVEN_OWNER


def test_recent_terminal_entry_is_kept(created_paths: list[Path]) -> None:
    video = _video()
    unit = _write(
        get_runtime_paths().transcoding / f"anonymizing_{video.raw_video_hash}",
        age_seconds=60,
    )
    created_paths.append(unit)

    item = _classify(unit, TransientArtifactOwnerKind.VIDEO_RAW_HASH)

    assert item.decision is TransientArtifactDecision.TOO_RECENT


def test_symbolic_link_is_never_followed_or_deleted(
    created_paths: list[Path],
) -> None:
    video = _video()
    unit = ensure_directory(
        get_runtime_paths().transcoding / f"anonymizing_{video.raw_video_hash}"
    )
    created_paths.append(unit)
    target = _write(unit / "frame.png")
    (unit / "link.png").symlink_to(target)

    item = _classify(unit, TransientArtifactOwnerKind.VIDEO_RAW_HASH)

    assert item.decision is TransientArtifactDecision.UNSAFE_LINK


def _terminal_units(created_paths: list[Path]) -> tuple[Path, Path, Path]:
    video = _video()
    key_id = uuid4()
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind="processed",
        status=VideoHlsArtifact.Status.READY,
        key_id=key_id,
    )
    terminal_hls = _hls_unit(created_paths, video, str(key_id))
    stale_scratch = _write(
        get_runtime_paths().transcoding / f"upload-source-{uuid4().hex}.mp4"
    )
    created_paths.append(stale_scratch)
    workspace = ensure_directory(
        get_runtime_paths().transcoding / f"anonymizing_{video.raw_video_hash}"
    )
    created_paths.append(workspace)
    _write(workspace / "frame.png")
    _backdate(workspace, STALE_AGE_SECONDS)
    return terminal_hls, stale_scratch, workspace


@pytest.mark.parametrize("apply", [False, True])
def test_cleanup_run_deletes_only_terminal_units_when_applied(
    created_paths: list[Path], apply: bool
) -> None:
    terminal_hls, stale_scratch, workspace = _terminal_units(created_paths)

    result = run_transient_artifact_cleanup(
        apply=apply, grace_seconds=GRACE_SECONDS, limit=25
    )

    by_path = {item.path: item for item in result.items}
    assert by_path[terminal_hls].decision is TransientArtifactDecision.DELETE
    assert by_path[workspace].decision is TransientArtifactDecision.DELETE
    assert by_path[stale_scratch].decision is TransientArtifactDecision.UNPROVEN_OWNER
    assert terminal_hls.exists() is not apply
    assert workspace.exists() is not apply
    assert stale_scratch.exists()
    assert by_path[terminal_hls].deleted is apply


def test_cleanup_run_respects_deletion_limit(created_paths: list[Path]) -> None:
    terminal_hls, _stale_scratch, workspace = _terminal_units(created_paths)

    result = run_transient_artifact_cleanup(
        apply=True, grace_seconds=GRACE_SECONDS, limit=1
    )

    assert result.deleted_count == 1
    assert terminal_hls.exists() != workspace.exists()


def test_invalid_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        run_transient_artifact_cleanup(
            apply=False, grace_seconds=GRACE_SECONDS, limit=0
        )


def test_single_transient_reap_deletes_terminal_unit(created_paths: list[Path]) -> None:
    terminal_hls, stale_scratch, _workspace = _terminal_units(created_paths)

    deleted = reap_transient_artifact(terminal_hls, grace_seconds=GRACE_SECONDS)
    kept = reap_transient_artifact(stale_scratch, grace_seconds=GRACE_SECONDS)

    assert deleted.deleted and not terminal_hls.exists()
    assert kept.decision is TransientArtifactDecision.UNPROVEN_OWNER
    assert not kept.deleted and stale_scratch.exists()


def test_single_transient_reap_rejects_unlisted_paths() -> None:
    with pytest.raises(ValueError, match="not a transient artifact unit"):
        reap_transient_artifact(
            get_runtime_paths().transcoding / "missing" / "nested",
            grace_seconds=GRACE_SECONDS,
        )
