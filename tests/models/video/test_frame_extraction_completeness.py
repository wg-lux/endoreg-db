from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.services.anonymization import AnonymizationService
from endoreg_db.services.media_integrity import mark_video_integrity_lost


def _video(tmp_path: Path, *, frame_count: int = 3) -> VideoFile:
    center = Center.objects.create(name=f"frame-complete-{uuid.uuid4().hex[:8]}")
    frame_dir = tmp_path / f"frames-{uuid.uuid4().hex[:8]}"
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"frame-complete-{uuid.uuid4().hex}",
        frame_count=frame_count,
        frame_dir=str(frame_dir),
    )
    video.initialize_frames()
    return video


@pytest.mark.django_db
def test_anonymize_refuses_video_marked_integrity_lost(tmp_path: Path) -> None:
    video = _video(tmp_path, frame_count=3)

    mark_video_integrity_lost(video, "frame cache remains invalid")

    video.refresh_from_db()
    state = video.get_or_create_state()
    state.refresh_from_db()
    assert state.processing_error is True
    assert state.anonymization_status == AnonymizationState.FAILED
    status_payload = AnonymizationService.get_status(video.pk, kind="video")
    assert status_payload is not None
    assert status_payload["anonymization_status"] == AnonymizationState.FAILED.value
    assert status_payload["integrity_status"] == "lost"

    with pytest.raises(ValueError, match="failed/lost"):
        video.anonymize(delete_original_raw=True)


@pytest.mark.parametrize(
    "numbers,complete", [([0, 1, 2], True), ([0, 2], False), ([1, 2, 3], False)]
)
def test_legacy_frame_manifest_is_read_only(
    tmp_path: Path, numbers: list[int], complete: bool
) -> None:
    from endoreg_db.services.video_files._frames._extract_frames import (
        build_frame_cache_manifest,
    )

    for number in numbers:
        (tmp_path / f"frame_{number:07d}.jpg").write_bytes(b"legacy")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    manifest = build_frame_cache_manifest(tmp_path, expected_count=3, ext="jpg")
    assert manifest.is_exact_complete is complete
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
