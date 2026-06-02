from __future__ import annotations

import io

import pytest

from endoreg_db.utils.video import frame_stream


class FakeStreamingProcess:
    def __init__(self, payload: bytes, *, returncode: int = 0, stderr: bytes = b""):
        self.stdout = io.BytesIO(payload)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.waited = False

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode

    def poll(self):
        if self.waited or self.terminated or self.killed:
            return self.returncode
        return None

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


def _patch_stream_metadata(monkeypatch):
    monkeypatch.setattr(frame_stream, "_resolve_ffmpeg_executable", lambda: "/ffmpeg")
    monkeypatch.setattr(
        frame_stream,
        "get_stream_info",
        lambda path: {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 2,
                    "height": 1,
                    "avg_frame_rate": "2/1",
                }
            ]
        },
    )


@pytest.mark.unit
def test_iter_video_path_frame_samples_yields_frame_numbers_and_timestamps(
    monkeypatch,
    tmp_path,
):
    _patch_stream_metadata(monkeypatch)
    frame_bytes = bytes(range(12))
    created_processes: list[FakeStreamingProcess] = []

    def fake_popen(command, **kwargs):
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
def test_read_video_path_frame_sample_decodes_requested_frame(monkeypatch, tmp_path):
    _patch_stream_metadata(monkeypatch)
    created_commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
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
def test_read_video_path_frame_sample_raises_when_frame_is_missing(
    monkeypatch,
    tmp_path,
):
    _patch_stream_metadata(monkeypatch)

    def fake_popen(command, **kwargs):
        return FakeStreamingProcess(b"")

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="produced no decoded frame"):
        frame_stream.read_video_path_frame_sample(
            tmp_path / "video.mp4", frame_number=7
        )


@pytest.mark.unit
def test_iter_video_path_frame_samples_cleans_process_on_early_close(
    monkeypatch,
    tmp_path,
):
    _patch_stream_metadata(monkeypatch)
    created_processes: list[FakeStreamingProcess] = []

    def fake_popen(command, **kwargs):
        process = FakeStreamingProcess(bytes(range(12)))
        created_processes.append(process)
        return process

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    iterator = frame_stream.iter_video_path_frame_samples(tmp_path / "video.mp4")
    next(iterator)
    iterator.close()

    assert created_processes[0].terminated is True


@pytest.mark.unit
def test_iter_video_path_frame_samples_raises_on_ffmpeg_error(monkeypatch, tmp_path):
    _patch_stream_metadata(monkeypatch)

    def fake_popen(command, **kwargs):
        return FakeStreamingProcess(b"", returncode=1, stderr=b"decode failed")

    monkeypatch.setattr(frame_stream.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="decode failed"):
        list(frame_stream.iter_video_path_frame_samples(tmp_path / "video.mp4"))
