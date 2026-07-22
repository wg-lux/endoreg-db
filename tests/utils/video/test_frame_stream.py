from __future__ import annotations

import io
import shutil
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.core.files.base import ContentFile
from pytest import MonkeyPatch

import endoreg_db.utils.frame_stream as frame_stream
from endoreg_db.utils.encryption.encrypted import EncryptedStorage


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
        "2.500000000",
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
def test_read_video_file_frame_jpeg_uses_authoritative_pts_and_seekable_ranges(
    monkeypatch: MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    field_file = SimpleNamespace(name="processed/video.mp4", storage=object())

    class FakeVideo:
        video_hash = "abc123"
        raw_file = SimpleNamespace(name="raw/video.mp4", storage=object())
        processed_file = field_file

        def get_fps(self) -> float:
            raise AssertionError("authoritative PTS must replace FPS-derived seeking")

        def frame_number_to_s(self, frame_number: int) -> float:
            assert frame_number == 144224
            return 123.456789123

    @contextmanager
    def fake_seekable_input(
        selected_field_file: object,
    ) -> Generator[object, None, None]:
        assert selected_field_file is field_file
        yield SimpleNamespace(
            url="http://127.0.0.1:43210/token/video.mp4",
            plaintext_size=10_000,
        )

    created_commands: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeStreamingProcess:
        created_commands.append(command)
        return FakeStreamingProcess(b"\xff\xd8encoded-jpeg")

    def no_local_plaintext_path(field_file_value: object) -> None:
        return None

    def supports_decrypted_ranges(field_file_value: object) -> bool:
        return True

    monkeypatch.setattr(
        frame_stream,
        "maybe_local_plaintext_path",
        no_local_plaintext_path,
    )
    monkeypatch.setattr(
        frame_stream,
        "field_file_has_decrypted_range_storage",
        supports_decrypted_ranges,
    )
    monkeypatch.setattr(frame_stream, "serve_seekable_media_input", fake_seekable_input)
    monkeypatch.setattr(frame_stream, "_resolve_ffmpeg_executable", lambda: "/ffmpeg")
    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    sample = frame_stream.read_video_file_frame_jpeg(
        cast(Any, FakeVideo()),
        frame_number=144224,
        file_type="processed",
    )

    assert sample.timestamp == 123.456789123
    command = created_commands[0]
    assert command[command.index("-ss") + 1] == "123.456789123"
    assert command[command.index("-seekable") + 1] == "1"
    assert command[command.index("-i") + 1].startswith("http://127.0.0.1:")


@pytest.mark.ffmpeg
@pytest.mark.video
def test_read_video_file_frame_jpeg_decodes_from_encrypted_ranges(
    tmp_path: Path,
) -> None:
    ffmpeg_executable = shutil.which("ffmpeg")
    if ffmpeg_executable is None:
        pytest.skip("ffmpeg is not available")

    source_path = tmp_path / "source.mp4"
    completed = frame_stream.subprocess.run(
        [
            ffmpeg_executable,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:r=10:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            "-y",
            str(source_path),
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        pytest.skip("ffmpeg test fixture could not be generated")

    storage = EncryptedStorage(
        location=tmp_path / "encrypted",
        master_key=b"k" * 32,
        chunk_size=128,
    )
    stored_name = storage.save("video.mp4", ContentFile(source_path.read_bytes()))
    field_file = SimpleNamespace(name=stored_name, storage=storage)

    class FakeVideo:
        video_hash = "encrypted-range-video"
        raw_file = field_file
        processed_file = field_file

        def get_fps(self) -> float:
            return 10.0

        def frame_number_to_s(self, frame_number: int) -> float:
            return frame_number / 10.0

    sample = frame_stream.read_video_file_frame_jpeg(
        cast(Any, FakeVideo()),
        frame_number=5,
        file_type="processed",
    )

    assert sample.frame_number == 5
    assert sample.timestamp == 0.5
    assert sample.image_bytes.startswith(b"\xff\xd8")


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
