from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from endoreg_db.services.video_storage import transcoding as video_transcoding
from endoreg_db.services.video_storage import canonical_timelines
from tests.helpers.canonical_timestamps import decoded_test_timestamps
from tests.services.test_video_processed_transcode_encryption import probe
from endoreg_db.schemas.video_storage import VideoArtifactProbe


pytestmark = pytest.mark.no_db


@pytest.fixture
def directory_timestamps(
    monkeypatch: MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    def fake_probe(_path: Path) -> VideoArtifactProbe:
        value = probe()
        return value.model_copy(
            update={
                "timeline": value.timeline.model_copy(
                    update={
                        "fps_num": int(float(getattr(request, "param", 25)) * 1000),
                        "fps_den": 1000,
                    }
                )
            }
        )

    monkeypatch.setattr(video_transcoding, "probe_video_artifact", fake_probe)
    monkeypatch.setattr(
        canonical_timelines, "probe_video_frame_timestamps", decoded_test_timestamps
    )


def _fake_compliant_video_format(path: Path) -> SimpleNamespace:
    return SimpleNamespace(compliant=True, reasons=[], error="")


def _fake_50_fps_stream_info(path: Path) -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "avg_frame_rate": "50/1",
            }
        ]
    }


def _fake_30000_1001_stream_info(path: Path) -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "avg_frame_rate": "30000/1001",
            }
        ]
    }


@pytest.mark.unit
@pytest.mark.usefixtures("directory_timestamps")
@pytest.mark.parametrize(
    "directory_timestamps", [25, 50, 59.7979, 60, 120, 240], indirect=True
)
def test_transcode_video_directory_stages_and_moves_output(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    source = input_dir / "clip.avi"
    source.write_bytes(b"raw-video")

    captured: dict[str, object] = {}

    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> Path:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["kwargs"] = kwargs
        output_path.write_bytes(b"standardized-video")
        return output_path

    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "transcode_video",
        fake_transcode_video,
    )
    monkeypatch.setattr(
        video_transcoding,
        "classify_video_format",
        _fake_compliant_video_format,
    )
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "get_stream_info",
        _fake_50_fps_stream_info,
    )

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
        force_cpu=True,
        quality_mode="quality",
    )

    destination = output_dir / "clip.mp4"
    assert summary.scanned_files == 1
    assert summary.transcoded_files == 1
    assert summary.failed_files == 0
    assert summary.target_fps is None
    assert destination.read_bytes() == b"standardized-video"
    assert captured["input_path"] == source.resolve()
    assert captured["output_path"] != destination
    assert captured["kwargs"] == {
        "extra_args": [
            "-profile:v",
            "high",
            "-vf",
            "scale=iw:ih:in_range=auto:out_range=full,format=yuv420p",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "pc",
            "-copyts",
            "-fps_mode",
            "passthrough",
            "-enc_time_base",
            "demux",
        ],
        "quality_mode": "quality",
        "force_cpu": True,
    }


@pytest.mark.unit
def test_transcode_video_directory_dry_run_does_not_create_output_dir(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
        dry_run=True,
    )

    assert summary.scanned_files == 1
    assert summary.planned_files == 1
    assert summary.transcoded_files == 0
    assert not output_dir.exists()
    assert summary.reports[0].destination == str((output_dir / "clip.mp4").resolve())


@pytest.mark.unit
@pytest.mark.parametrize("target_fps", [29.97, 50.0])
def test_transcode_video_directory_uses_explicit_target_fps(
    monkeypatch: MonkeyPatch, tmp_path: Path, target_fps: float
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")

    captured: dict[str, object] = {}

    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> Path:
        captured["kwargs"] = kwargs
        output_path.write_bytes(b"standardized-video")
        return output_path

    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "transcode_video",
        fake_transcode_video,
    )
    monkeypatch.setattr(
        video_transcoding,
        "classify_video_format",
        _fake_compliant_video_format,
    )
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "get_stream_info",
        _fake_30000_1001_stream_info,
    )

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
        target_fps=target_fps,
    )

    assert summary.transcoded_files == 1
    assert summary.target_fps == target_fps
    assert captured["kwargs"] == {
        "extra_args": [
            "-profile:v",
            "high",
            "-vf",
            "scale=iw:ih:in_range=auto:out_range=full,format=yuv420p",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "pc",
            "-fpsmax",
            f"{target_fps:g}",
        ],
        "quality_mode": "balanced",
        "force_cpu": False,
    }


@pytest.mark.unit
def test_transcode_video_directory_skips_existing_destination(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")
    destination = output_dir / "clip.mp4"
    destination.write_bytes(b"existing-video")

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
    )

    assert summary.skipped_files == 1
    assert summary.transcoded_files == 0
    assert destination.read_bytes() == b"existing-video"
    assert (
        summary.reports[0].action
        == video_transcoding.VideoTranscodeAction.SKIP_EXISTING
    )


@pytest.mark.parametrize("fps", [None, "0/0", "not-a-rate"])
def test_unknown_source_fps_still_fails_decoder_validation(
    monkeypatch: MonkeyPatch, tmp_path: Path, fps: str | None
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"source")
    stream: dict[str, object] = {
        "codec_type": "video",
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "width": 64,
        "height": 64,
        "duration": "1",
        "avg_frame_rate": fps,
    }
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "get_stream_info",
        Mock(return_value={"streams": [stream]}),
    )
    capture = Mock()
    encode = Mock()
    monkeypatch.setattr(video_transcoding, "capture_canonical_timeline", capture)
    monkeypatch.setattr(video_transcoding.ffmpeg_wrapper, "transcode_video", encode)
    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=tmp_path / "output",
        allow_unmanaged_output=True,
    )
    assert summary.failed_files == 1
    assert summary.reports[0].error
    capture.assert_not_called()
    encode.assert_not_called()


@pytest.mark.ffmpeg
@pytest.mark.parametrize("fps", [59.7979, 60.0, 120.0, 240.0])
def test_real_high_fps_transcode_preserves_complete_native_timestamps(
    tmp_path: Path, fps: float, monkeypatch: MonkeyPatch
) -> None:
    from endoreg_db.services.video_storage import frame_provenance
    from endoreg_db.utils.transcode_execution import get_stream_info

    # This integration lane must never inherit the suite's fabricated probe fallback.
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper, "get_stream_info", get_stream_info
    )
    persist_proof = Mock()
    monkeypatch.setattr(frame_provenance, "persist_frame_provenance", persist_proof)
    executable = video_transcoding.ffmpeg_wrapper.resolve_ffmpeg_executable()
    assert executable is not None, "FFmpeg is required for this integration test"
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    source = input_dir / "source.mp4"
    subprocess.run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=64x64:rate={fps:g}:duration=0.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "pc",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    source_bytes = source.read_bytes()
    source_timeline = canonical_timelines.capture_canonical_timeline(
        source, video_transcoding.probe_video_artifact(source)
    )
    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=tmp_path / "output",
        allow_unmanaged_output=True,
        force_cpu=True,
        quality_mode="fast",
    )
    assert summary.failed_files == 0, summary.as_dict()
    assert summary.transcoded_files == 1
    destination = Path(summary.reports[0].destination)
    output_timeline = canonical_timelines.capture_canonical_timeline(
        destination, video_transcoding.probe_video_artifact(destination)
    )
    assert (
        source_timeline.presentation_timestamps
        == output_timeline.presentation_timestamps
    )
    assert source_timeline.time_base_num == output_timeline.time_base_num
    assert source_timeline.time_base_den == output_timeline.time_base_den
    assert source.read_bytes() == source_bytes
    assert video_transcoding.classify_video_format(destination).compliant
    persist_proof.assert_called_once()
