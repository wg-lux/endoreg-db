from uuid import uuid4

import pytest
from django.conf import settings
from django.core.cache import cache
from pydantic import ValidationError

from endoreg_db.config import env
from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.schemas.processed_video_cleanup import (
    ProcessedGenerationCleanupReceipt,
    ProcessedGenerationCleanupResult,
)
from endoreg_db.services.hub import cleanup
from endoreg_db.services.video_storage import generation_cleanup
from endoreg_db.tasks import cleanup_media_sources_task
from endoreg_db.utils.file_operations import (
    advisory_file_lock,
    atomic_write_file,
    safe_unlink_file,
)
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def reset_cleanup_cursor() -> None:
    safe_unlink_file(
        get_runtime_paths().manifest_dir / "periodic_media_cleanup.json",
        missing_ok=True,
    )


@pytest.mark.parametrize("apply", [False, True])
def test_periodic_cleanup_respects_apply_gate_and_receipt_selection(
    monkeypatch: pytest.MonkeyPatch, apply: bool
) -> None:
    center = Center.objects.create(name=f"periodic-{uuid4().hex}")
    receipt = ProcessedGenerationCleanupReceipt(
        source_name="old.mp4",
        source_sha256="a" * 64,
        replacement_name="new.mp4",
        replacement_sha256="b" * 64,
    )
    pending = VideoFile.objects.create(
        center=center,
        raw_video_hash=uuid4().hex,
        meta={"processed_generation_cleanup": [receipt.model_dump(mode="json")]},
    )
    VideoFile.objects.create(
        center=center,
        raw_video_hash=uuid4().hex,
        meta={"processed_generation_cleanup": []},
    )
    calls: list[tuple[int, bool]] = []

    def sources(*, apply: bool, limit: int) -> cleanup.UploadSourceReaperResult:
        assert limit == 25
        calls.append((0, apply))
        return cleanup.UploadSourceReaperResult(items=())

    def generations(video_id: int, *, apply: bool) -> ProcessedGenerationCleanupResult:
        calls.append((video_id, apply))
        return ProcessedGenerationCleanupResult(
            video_id=video_id,
            cleaned=int(apply),
            pending=int(not apply),
            reason="cleaned" if apply else "dry_run",
        )

    monkeypatch.setenv("UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED", str(apply).lower())
    monkeypatch.setattr(cleanup, "run_upload_job_source_reaper", sources)
    monkeypatch.setattr(
        generation_cleanup, "cleanup_processed_video_generations", generations
    )
    result = cleanup_media_sources_task()
    assert calls == [(0, apply), (pending.pk, apply)]
    assert result["generations_cleaned"] == int(apply)
    assert result["generations_pending"] == int(not apply)


def test_periodic_cleanup_is_routed_and_expires_before_next_run() -> None:
    entry = settings.CELERY_BEAT_SCHEDULE["cleanup-media-sources"]
    assert entry["task"] == "endoreg_db.cleanup_media_sources"
    assert entry["schedule"] == 900
    assert entry["options"]["queue"] == settings.CELERY_MAINTENANCE_QUEUE
    assert entry["options"]["expires"] < entry["schedule"]


def test_periodic_cleanup_rotates_past_blocked_videos_without_master_receipts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(name=f"rotation-{uuid4().hex}")
    ids: list[int] = []
    for _ in range(26):
        video = VideoFile.objects.create(center=center, raw_video_hash=uuid4().hex)
        VideoHlsArtifact.objects.create(
            video=video, artifact_kind="processed", status="superseded"
        )
        ids.append(video.pk)
    calls: list[int] = []

    def derivatives(video_id: int, *, apply: bool) -> ProcessedGenerationCleanupResult:
        assert apply
        calls.append(video_id)
        return ProcessedGenerationCleanupResult(
            video_id=video_id, pending=1, reason="replacement_not_ready"
        )

    monkeypatch.setattr(env, "upload_job_source_reaper_apply_enabled", lambda: True)
    monkeypatch.setattr(generation_cleanup, "cleanup_superseded_hls", derivatives)
    assert cleanup_media_sources_task()["hls_artifacts_pending"] == 25
    assert calls == ids[:25]
    cache.clear()  # A fresh worker has no process-local cache.
    assert cleanup_media_sources_task()["hls_artifacts_pending"] == 1
    assert calls == ids
    cleanup_media_sources_task()
    assert calls == ids + ids[:25]


@pytest.mark.parametrize(
    "content",
    [b"broken", b'{"video_id": -1}', b'{"video_id": "1"}', b'{"schema_version": 2}'],
)
def test_periodic_cleanup_rejects_invalid_cursor_before_deletion(
    content: bytes,
) -> None:
    atomic_write_file(
        destination=get_runtime_paths().manifest_dir / "periodic_media_cleanup.json",
        content=[content],
    )
    with pytest.raises(ValidationError):
        cleanup_media_sources_task()


def test_periodic_cleanup_does_not_overlap_another_run() -> None:
    with advisory_file_lock(
        lock_path=get_runtime_paths().manifest_dir / ".periodic_media_cleanup.lock",
        timeout_seconds=0,
    ):
        with pytest.raises(TimeoutError):
            cleanup_media_sources_task()
