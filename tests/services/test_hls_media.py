from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, cast
from urllib.parse import urlsplit

import pytest
from django.core.files.base import ContentFile

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.services import hls_media
from endoreg_db.utils.ffmpeg_wrapper import resolve_ffmpeg_executable
from endoreg_db.utils.paths import EndoregPathsModel
from tests.helpers.hls import FakeHlsOutputRecorder

pytestmark = pytest.mark.django_db


class PlaintextLeakSpy:
    def __init__(self, *, needle: bytes, roots: tuple[Path, ...]) -> None:
        self.needle = needle
        self.roots = roots
        self._snapshot: dict[Path, tuple[int, int]] = {}

    def __enter__(self) -> "PlaintextLeakSpy":
        self._snapshot = self._snapshot_files()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.assert_no_plaintext_leak()

    def _snapshot_files(self) -> dict[Path, tuple[int, int]]:
        snapshot: dict[Path, tuple[int, int]] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    stat_result = path.stat()
                except OSError:
                    continue
                snapshot[path] = (stat_result.st_mtime_ns, stat_result.st_size)
        return snapshot

    def _new_or_modified_files(self) -> list[Path]:
        changed: list[Path] = []
        after = self._snapshot_files()
        for path, stat_payload in after.items():
            if self._snapshot.get(path) != stat_payload:
                changed.append(path)
        return changed

    def assert_no_plaintext_leak(self) -> None:
        for path in self._new_or_modified_files():
            try:
                with path.open("rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        assert self.needle not in chunk, (
                            f"Plaintext payload leaked into {path}"
                        )
            except OSError:
                continue


@pytest.fixture
def hls_center() -> Center:
    return Center.objects.create(
        name="hls-materialization-center",
        display_name="HLS Materialization Center",
    )


def _create_processed_video(
    *,
    center: Center,
    payload: bytes = b"plaintext mp4 payload",
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"hls-video-{payload.hex()}",
    )
    cast(Any, video.processed_file).save(
        "hls-source.mp4",
        ContentFile(payload),
        save=True,
    )
    return video


def _create_raw_video(
    *,
    center: Center,
    payload: bytes = b"raw plaintext mp4 payload",
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"hls-raw-video-{payload.hex()}",
    )
    cast(Any, video.raw_file).save(
        "hls-raw-source.mp4",
        ContentFile(payload),
        save=True,
    )
    return video


def test_ffmpeg_hls_command_uses_clinical_quality_h264_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hls_media.ffmpeg_wrapper,
        "resolve_ffmpeg_executable",
        lambda: "/usr/bin/ffmpeg",
    )

    command = cast(Any, hls_media)._ffmpeg_command(
        key_info_path=Path("/tmp/key_info.txt"),
        segment_pattern=Path("/tmp/seg_%03d.ts"),
        playlist_path=Path("/tmp/playlist.m3u8"),
        segment_base_url="/media/videos/1/hls/",
        input_arg="pipe:0",
    )

    assert command[command.index("-preset") + 1] == hls_media.HLS_VIDEO_PRESET
    assert command[command.index("-crf") + 1] == hls_media.HLS_VIDEO_CRF
    assert command[command.index("-profile:v") + 1] == hls_media.HLS_VIDEO_PROFILE
    assert command[command.index("-pix_fmt") + 1] == hls_media.HLS_VIDEO_PIXEL_FORMAT
    assert command[command.index("-codec:a") + 1] == hls_media.HLS_AUDIO_CODEC
    assert "0:v:0" in command
    assert "0:a?" in command
    assert "-level" not in command
    assert "-b:a" not in command


def _extract_hls_key_uri(playlist_text: str) -> str:
    key_uri_marker = 'URI="'
    for line in playlist_text.splitlines():
        if not line.startswith("#EXT-X-KEY:"):
            continue
        value_start = line.find(key_uri_marker)
        if value_start < 0:
            continue
        value_start += len(key_uri_marker)
        value_end = line.find('"', value_start)
        if value_end < 0:
            continue
        return line[value_start:value_end]
    raise AssertionError("HLS playlist does not contain an EXT-X-KEY URI")


def _playlist_segment_uris(playlist_text: str) -> list[str]:
    return [
        line
        for line in playlist_text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _assert_api_rooted_relative_uri(uri: str) -> None:
    parsed_uri = urlsplit(uri)
    assert parsed_uri.scheme == ""
    assert parsed_uri.netloc == ""
    assert parsed_uri.path.startswith("/endoreg-api/")


def _write_tiny_ffmpeg_mp4(output_path: Path) -> None:
    ffmpeg_executable = resolve_ffmpeg_executable()
    if ffmpeg_executable is None:
        pytest.skip("ffmpeg executable is not available")

    command = [
        ffmpeg_executable,
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=64x64:rate=5:duration=1",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=mono:sample_rate=8000",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")


def test_materialize_video_hls_streams_decrypted_source_and_cleans_temp_key(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_payload = b"source payload that should only exist in the stream"
    video = _create_processed_video(center=hls_center, payload=source_payload)
    paths = EndoregPathsModel.from_environment()
    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)

    with PlaintextLeakSpy(
        needle=source_payload,
        roots=(paths.transcoding, Path(tempfile.gettempdir())),
    ):
        result = hls_media.materialize_video_hls(video.pk, artifact_kind="processed")

    assert result.status == "materialized"
    assert fake_hls.source_payloads == [source_payload]
    assert fake_hls.key_info_records
    key_record = fake_hls.key_info_records[0]
    assert (
        key_record.key_uri
        == f"/endoreg-api/media/videos/{video.pk}/hls/key/{key_record.key_path.parent.name}/"
    )
    assert fake_hls.content_key_payloads[0] != source_payload
    assert len(fake_hls.content_key_payloads[0]) == hls_media.HLS_CONTENT_KEY_BYTES
    assert len(key_record.iv_hex) == hls_media.HLS_IV_HEX_LENGTH
    assert all(not record.key_path.exists() for record in fake_hls.key_info_records)

    artifact = VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")
    assert artifact.status == VideoHlsArtifact.Status.READY.value
    assert artifact.key_ciphertext is not None
    assert artifact.key_nonce is not None
    assert hls_media.unwrap_hls_content_key(artifact) != source_payload

    temp_key_dir = (
        paths.transcoding / "hls_key_material" / str(video.pk) / result.key_id
    )
    assert not temp_key_dir.exists()
    temp_output_dir = paths.transcoding / "hls_output" / str(video.pk) / result.key_id
    assert not temp_output_dir.exists()

    segment_dir = Path(paths.storage / result.segment_directory_relative_path)
    assert (segment_dir / "seg_000.ts").exists()
    assert not list(segment_dir.glob("*.key"))


def test_force_materialize_video_hls_removes_replaced_artifact_directory(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_payload = b"first encrypted processed file source"
    second_payload = b"second encrypted processed file source"
    video = _create_processed_video(center=hls_center, payload=first_payload)
    paths = EndoregPathsModel.from_environment()
    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)

    first = hls_media.materialize_video_hls(video.pk, artifact_kind="processed")
    first_segment_dir = Path(paths.storage / first.segment_directory_relative_path)
    assert first_segment_dir.exists()

    cast(Any, video.processed_file).save(
        "hls-source-reimport.mp4",
        ContentFile(second_payload),
        save=True,
    )

    second = hls_media.materialize_video_hls(
        video.pk,
        artifact_kind="processed",
        force=True,
    )
    second_segment_dir = Path(paths.storage / second.segment_directory_relative_path)

    assert second.status == "materialized"
    assert second.key_id != first.key_id
    assert fake_hls.source_payloads == [first_payload, second_payload]
    assert not first_segment_dir.exists()
    assert second_segment_dir.exists()


def test_force_materialize_video_hls_keeps_new_artifact_when_old_cleanup_fails(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    video = _create_processed_video(center=hls_center, payload=b"first source")
    paths = EndoregPathsModel.from_environment()
    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)

    first = hls_media.materialize_video_hls(video.pk, artifact_kind="processed")
    cast(Any, video.processed_file).save(
        "hls-source-cleanup-failure.mp4",
        ContentFile(b"replacement source"),
        save=True,
    )

    def fail_cleanup(snapshot: object) -> None:
        raise RuntimeError("old cleanup failed")

    monkeypatch.setattr(
        hls_media,
        "_cleanup_replaced_artifact",
        fail_cleanup,
        raising=True,
    )
    caplog.set_level("WARNING", logger=hls_media.__name__)

    second = hls_media.materialize_video_hls(
        video.pk,
        artifact_kind="processed",
        force=True,
    )

    assert second.status == "materialized"
    assert second.key_id != first.key_id
    assert (
        Path(paths.storage / second.segment_directory_relative_path) / "seg_000.ts"
    ).exists()
    assert "Could not remove replaced HLS artifact" in caplog.text


def test_materialized_playlist_uses_api_rooted_relative_key_and_segment_uris(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_processed_video(
        center=hls_center,
        payload=b"same origin playlist uri contract",
    )
    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)

    result = hls_media.materialize_video_hls(video.pk, artifact_kind="processed")

    paths = EndoregPathsModel.from_environment()
    playlist_path = Path(paths.storage / result.playlist_relative_path)
    playlist_text = playlist_path.read_text(encoding="utf-8")
    key_uri = _extract_hls_key_uri(playlist_text)
    segment_uris = _playlist_segment_uris(playlist_text)
    expected_key_uri = f"/endoreg-api/media/videos/{video.pk}/hls/key/{result.key_id}/"
    expected_segment_uri = (
        f"/endoreg-api/media/videos/{video.pk}/hls/segments/{result.key_id}/seg_000.ts"
    )

    assert key_uri == expected_key_uri
    assert segment_uris == [expected_segment_uri]
    for uri in [key_uri, *segment_uris]:
        _assert_api_rooted_relative_uri(uri)


def test_materialize_video_hls_ignores_existing_processed_streamable_source(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_payload = b"canonical field file source"
    video = _create_processed_video(
        center=hls_center,
        payload=canonical_payload,
    )
    paths = EndoregPathsModel.from_environment()
    streamable_relative_path = "streamable_videos/processed/hls-streamable-source.mp4"
    streamable_path = paths.storage / streamable_relative_path
    streamable_path.parent.mkdir(parents=True, exist_ok=True)
    streamable_payload = b"existing processed streamable payload"
    streamable_path.write_bytes(streamable_payload)
    video.processed_streamable_relative_path = streamable_relative_path
    video.save(update_fields=["processed_streamable_relative_path"])

    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)

    result = hls_media.materialize_video_hls(video.pk, artifact_kind="processed")

    assert result.status == "materialized"
    assert fake_hls.source_payloads == [canonical_payload]


@pytest.mark.ffmpeg
def test_materialize_video_hls_real_ffmpeg_commits_staged_output(
    hls_center: Center,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "hls-source.mp4"
    _write_tiny_ffmpeg_mp4(source_path)
    video = _create_processed_video(
        center=hls_center,
        payload=source_path.read_bytes(),
    )

    result = hls_media.materialize_video_hls(video.pk, artifact_kind="processed")

    artifact = VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")
    assert artifact.status == VideoHlsArtifact.Status.READY.value
    assert result.status == "materialized"
    assert result.segment_count >= 1

    paths = EndoregPathsModel.from_environment()
    playlist_path = Path(paths.storage / result.playlist_relative_path)
    segment_dir = Path(paths.storage / result.segment_directory_relative_path)
    segments = sorted(segment_dir.glob("seg_*.ts"))

    assert playlist_path.is_file()
    playlist_text = playlist_path.read_text(encoding="utf-8")
    assert "#EXTM3U" in playlist_text
    assert "#EXT-X-KEY" in playlist_text
    key_uri = _extract_hls_key_uri(playlist_text)
    segment_uris = _playlist_segment_uris(playlist_text)
    assert key_uri == f"/endoreg-api/media/videos/{video.pk}/hls/key/{result.key_id}/"
    assert segment_uris
    for segment_uri in segment_uris:
        assert segment_uri.startswith(
            f"/endoreg-api/media/videos/{video.pk}/hls/segments/{result.key_id}/seg_"
        )
        assert segment_uri.endswith(".ts")
    for uri in [key_uri, *segment_uris]:
        _assert_api_rooted_relative_uri(uri)
    assert len(segments) == result.segment_count
    assert all(segment.is_file() and segment.stat().st_size > 0 for segment in segments)
    assert not (
        paths.transcoding / "hls_output" / str(video.pk) / result.key_id
    ).exists()
    assert not list(segment_dir.glob("*.key"))


def test_materialize_video_hls_encrypts_raw_for_local_authenticated_playback(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_raw_video(center=hls_center, payload=b"raw local playback")
    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)

    result = hls_media.materialize_video_hls(video.pk, artifact_kind="raw")

    artifact = VideoHlsArtifact.objects.get(video=video, artifact_kind="raw")
    assert artifact.status == VideoHlsArtifact.Status.READY
    assert result.artifact_kind == "raw"
    assert Path(result.playlist_relative_path).parts[:3] == (
        "streamable_videos",
        "raw",
        "hls",
    )
    assert hls_media.unwrap_hls_content_key(artifact) != b"raw local playback"


def test_get_ready_hls_artifact_by_key_accepts_ready_raw_artifact(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_raw_video(center=hls_center, payload=b"ready raw")
    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)
    hls_media.materialize_video_hls(video.pk, artifact_kind="raw")
    artifact = VideoHlsArtifact.objects.get(video=video, artifact_kind="raw")

    resolved = hls_media.get_ready_hls_artifact_by_key(
        video=video,
        key_id=artifact.key_id,
    )

    assert resolved.pk == artifact.pk


def test_materialize_video_hls_failure_unlinks_partial_segments_and_keys(
    hls_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_processed_video(center=hls_center, payload=b"failing source")
    observed_temp_key_paths: list[Path] = []

    def fake_run_ffmpeg_hls(
        *,
        source: BinaryIO,
        source_file_name: str,
        source_size_bytes: int | None,
        temp_source_dir: Path,
        key_info_path: Path,
        segment_pattern: Path,
        playlist_path: Path,
        segment_base_url: str,
    ) -> None:
        _ = source_file_name
        _ = source_size_bytes
        _ = temp_source_dir
        _ = source.read()
        _ = playlist_path
        _ = segment_base_url
        key_info_lines = key_info_path.read_text(encoding="utf-8").splitlines()
        observed_temp_key_paths.append(Path(key_info_lines[1]))
        segment_pattern.parent.mkdir(parents=True, exist_ok=True)
        (segment_pattern.parent / "seg_000.ts").write_bytes(b"partial segment")
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_run_ffmpeg_hls)

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        hls_media.materialize_video_hls(video.pk, artifact_kind="processed")

    artifact = VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")
    assert artifact.status == VideoHlsArtifact.Status.FAILED.value
    assert artifact.key_ciphertext is None
    assert artifact.key_nonce is None
    assert artifact.playlist_relative_path == ""
    assert artifact.segment_directory_relative_path == ""
    assert all(not path.exists() for path in observed_temp_key_paths)

    paths = EndoregPathsModel.from_environment()
    failed_hls_dir = (
        paths.storage
        / "streamable_videos"
        / "processed"
        / "hls"
        / str(video.uuid)
        / str(artifact.key_id)
    )
    assert not failed_hls_dir.exists()
    temp_key_dir = (
        paths.transcoding / "hls_key_material" / str(video.pk) / str(artifact.key_id)
    )
    assert not temp_key_dir.exists()
    temp_output_dir = (
        paths.transcoding / "hls_output" / str(video.pk) / str(artifact.key_id)
    )
    assert not temp_output_dir.exists()
