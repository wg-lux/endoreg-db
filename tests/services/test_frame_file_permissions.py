from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from endoreg_db.models import Center, Frame, VideoFile
from endoreg_db.services.report_frame_export import materialized_report_frame
from endoreg_db.utils.frame_stream import EncodedFrameSample
from endoreg_db.utils.paths import get_runtime_paths


@pytest.mark.django_db
@pytest.mark.parametrize("consumer_fails", [False, True])
def test_report_frame_export_is_protected_and_always_removed(
    consumer_fails: bool,
) -> None:
    center = Center.objects.create(name="report-frame-permissions")
    video = VideoFile.objects.create(
        center=center, raw_video_hash="report-frame-permissions"
    )
    state = video.get_or_create_state()
    state.anonymized = state.anonymization_validated = True
    state.save(update_fields=["anonymized", "anonymization_validated"])
    frame = Frame.objects.create(video=video, frame_number=3, timestamp=0.17)
    sample = EncodedFrameSample(
        frame_number=3, timestamp=0.17, content_type="image/jpeg", image_bytes=b"image"
    )
    paths: list[Path] = []
    with patch(
        "endoreg_db.services.report_frame_export.read_video_file_frame_jpeg",
        return_value=sample,
    ):
        try:
            with materialized_report_frame(frame) as path:
                paths.append(path)
                assert path.is_relative_to(get_runtime_paths().transcoding)
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
                assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
                assert path.read_bytes() == b"image"
                if consumer_fails:
                    raise RuntimeError("renderer failed")
        except RuntimeError as exc:
            assert consumer_fails and str(exc) == "renderer failed"
    assert paths and all(not path.parent.exists() for path in paths)
    frame.refresh_from_db()
    assert not frame.is_extracted and frame.timestamp == 0.17
