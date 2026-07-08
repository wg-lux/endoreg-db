from __future__ import annotations

import io
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import pytest
from pytest import MonkeyPatch

import endoreg_db.utils.frame_stream as frame_stream


class FakeStreamingProcess:
    def __init__(self, payload: bytes, *, returncode: int = 0, stderr: bytes = b""):
        self.stdout = io.BytesIO(payload)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.waited = False

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self.returncode

    def communicate(self) -> tuple[bytes, bytes]:
        self.waited = True
        stdout = self.stdout.read()
        stderr = self.stderr.read()
        return stdout, stderr

    def poll(self) -> int | None:
        if self.waited or self.terminated or self.killed:
            return self.returncode
        return None

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _patch_stream_metadata(monkeypatch: MonkeyPatch) -> None:
    def fake_stream_info(path: str | Path) -> dict[str, object]:
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 2,
                    "height": 1,
                    "avg_frame_rate": "2/1",
                }
            ]
        }

    monkeypatch.setattr(frame_stream, "_resolve_ffmpeg_executable", lambda: "/ffmpeg")
    monkeypatch.setattr(frame_stream, "get_stream_info", fake_stream_info)


@pytest.mark.unit
def test_iter_video_path_frame_samples_yields_frame_numbers_and_timestamps(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_stream_metadata(monkeypatch)
    frame_bytes = bytes(range(12))
    created_processes: list[FakeStreamingProcess] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        process = FakeStreamingProcess(frame_bytes)
        created_processes.append(process)
        return process

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    samples = list(frame_stream.iter_video_path_frame_samples(tmp_path / "video.mp4"))

    assert [sample.frame_number for sample in samples] == [0, 1]
    assert [sample.timestamp for sample in samples] == [0.0, 0.5]
    assert samples[0].rgb_frame.shape == (1, 2, 3)
    assert samples[1].rgb_frame.tolist() == [[[6, 7, 8], [9, 10, 11]]]
    assert created_processes[0].waited is True
    assert created_processes[0].terminated is False


@pytest.mark.unit
def test_read_video_path_frame_sample_decodes_requested_frame(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stream_metadata(monkeypatch)
    created_commands: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        created_commands.append(command)
        return FakeStreamingProcess(bytes(range(6)))

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    sample = frame_stream.read_video_path_frame_sample(
        tmp_path / "video.mp4",
        frame_number=5,
    )

    assert sample.frame_number == 5
    assert sample.timestamp == 2.5
    assert sample.rgb_frame.tolist() == [[[0, 1, 2], [3, 4, 5]]]
    assert "-frames:v" in created_commands[0]
    assert "select='eq(n,5)'" in created_commands[0]


@pytest.mark.unit
def test_read_video_path_frame_jpeg_uses_timestamp_seek_and_mjpeg_pipe(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(frame_stream, "_resolve_ffmpeg_executable", lambda: "/ffmpeg")
    created_commands: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        created_commands.append(command)
        return FakeStreamingProcess(b"\xff\xd8encoded-jpeg")

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    sample = frame_stream.read_video_path_frame_jpeg(
        tmp_path / "video.mp4",
        frame_number=5,
        fps_hint=2.0,
    )

    assert sample.frame_number == 5
    assert sample.timestamp == 2.5
    assert sample.content_type == "image/jpeg"
    assert sample.image_bytes == b"\xff\xd8encoded-jpeg"
    assert created_commands[0] == [
        "/ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-ss",
        "2.500000",
        "-i",
        str(tmp_path / "video.mp4"),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]


@pytest.mark.unit
def test_read_video_path_frame_jpeg_raises_when_frame_is_missing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(frame_stream, "_resolve_ffmpeg_executable", lambda: "/ffmpeg")

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        return FakeStreamingProcess(b"")

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="produced no encoded frame"):
        frame_stream.read_video_path_frame_jpeg(
            tmp_path / "video.mp4",
            frame_number=7,
            fps_hint=2.0,
        )


@pytest.mark.unit
def test_read_video_path_frame_sample_raises_when_frame_is_missing(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_stream_metadata(monkeypatch)

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        return FakeStreamingProcess(b"")

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="produced no decoded frame"):
        frame_stream.read_video_path_frame_sample(
            tmp_path / "video.mp4", frame_number=7
        )


@pytest.mark.unit
def test_iter_video_path_frame_samples_cleans_process_on_early_close(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_stream_metadata(monkeypatch)
    created_processes: list[FakeStreamingProcess] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        process = FakeStreamingProcess(bytes(range(12)))
        created_processes.append(process)
        return process

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    iterator = cast(
        Generator[frame_stream.FrameSample, None, None],
        frame_stream.iter_video_path_frame_samples(tmp_path / "video.mp4"),
    )
    next(iterator)
    iterator.close()

    assert created_processes[0].terminated is True


@pytest.mark.unit
def test_iter_video_path_frame_samples_raises_on_ffmpeg_error(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    _patch_stream_metadata(monkeypatch)

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        return FakeStreamingProcess(b"", returncode=1, stderr=b"decode failed")

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="decode failed"):
        list(frame_stream.iter_video_path_frame_samples(tmp_path / "video.mp4"))
