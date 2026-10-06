from uuid import uuid4

import pytest
from django.conf import settings

from endoreg_db.config import env
from endoreg_db.models import Center, VideoFile
from endoreg_db.schemas.processed_video_cleanup import (
    ProcessedGenerationCleanupReceipt,
    ProcessedGenerationCleanupResult,
)
from endoreg_db.services.hub import cleanup
from endoreg_db.services.video_storage import generation_cleanup
from endoreg_db.tasks import cleanup_media_sources_task

pytestmark = pytest.mark.django_db


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

    monkeypatch.setattr(env, "upload_job_source_reaper_apply_enabled", lambda: apply)
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
