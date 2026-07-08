from __future__ import annotations

import json
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

import pytest
from django.contrib.auth.models import User
from django.core import signals
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.services import hls_media
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.paths import EndoregPathsModel
from tests.helpers.hls import FakeHlsOutputRecorder

pytestmark = pytest.mark.django_db


class _SignalLike(Protocol):
    def connect(
        self,
        receiver: Callable[..., object],
        sender: object | None = None,
        weak: bool = True,
        dispatch_uid: object | None = None,
    ) -> None: ...

    def disconnect(
        self,
        receiver: Callable[..., object] | None = None,
        sender: object | None = None,
        dispatch_uid: object | None = None,
    ) -> bool: ...


class _HlsArtifactIdentity(Protocol):
    key_id: UUID


@pytest.fixture
def streaming_profile_center() -> Center:
    return Center.objects.create(
        name="streaming-profile-center",
        display_name="Streaming Profile Center",
    )


def _create_processed_video(center: Center) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash="streaming-profile-video",
        width=1920,
        height=1080,
    )
    cast(Any, video.processed_file).save(
        "streaming-profile-source.mp4",
        ContentFile(b"streaming profile source payload"),
        save=True,
    )
    return video


def _make_processed_streamable(video: VideoFile) -> None:
    paths = EndoregPathsModel.from_environment()
    payload = b"\x00\x00\x00\x18ftypmp42profile-streamable"
    streamable_path = (
        paths.storage
        / "streamable_videos"
        / "processed"
        / "streaming-profile-video.mp4"
    )
    atomic_write_file(
        destination=streamable_path,
        content=(payload,),
        required_bytes=len(payload),
    )
    video.storage_mode = VideoStorageMode.STREAMABLE.value
    video.processed_streamable_relative_path = streamable_path.relative_to(
        paths.storage
    ).as_posix()
    video.save(
        update_fields=[
            "storage_mode",
            "processed_streamable_relative_path",
            "date_modified",
        ]
    )


def _materialize_fake_hls(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
) -> VideoHlsArtifact:
    fake_hls = FakeHlsOutputRecorder(include_version_tag=False)
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)
    hls_media.materialize_video_hls(video.pk, artifact_kind="processed")
    return VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")


def test_profile_video_streaming_profiles_hls_and_mp4_nginx_handoff(
    streaming_profile_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    video = _create_processed_video(streaming_profile_center)
    hls_artifact = _materialize_fake_hls(video, monkeypatch)
    _make_processed_streamable(video)
    profile_path = tmp_path / "video-streaming.prof"
    summary_path = tmp_path / "video-streaming.txt"
    user_count_before = User.objects.count()
    lease_count_before = MediaOperationLease.objects.count()
    dispatch_uid = "profile-video-streaming-test-request-finished"

    def fail_on_request_finished(sender: object, **kwargs: object) -> None:
        _ = sender, kwargs
        raise AssertionError("profile_video_streaming must not emit request_finished")

    stdout = StringIO()
    request_finished = cast(_SignalLike, signals.request_finished)
    request_finished.connect(
        fail_on_request_finished,
        dispatch_uid=dispatch_uid,
        weak=False,
    )
    try:
        call_command(
            "profile_video_streaming",
            "--endpoint",
            "all",
            "--video-id",
            str(video.pk),
            "--iterations",
            "2",
            "--profile-output",
            str(profile_path),
            "--profile-summary-output",
            str(summary_path),
            "--json",
            stdout=stdout,
        )
    finally:
        request_finished.disconnect(dispatch_uid=dispatch_uid)

    payload = json.loads(stdout.getvalue())
    hls_payload = payload["hls"]
    mp4_payload = payload["mp4"]
    hls_target = hls_payload["targets"][0]
    mp4_target = mp4_payload["targets"][0]

    assert payload["endpoint"] == "all"
    assert payload["iterations"] == 2
    assert payload["rolled_back"] is True
    assert payload["selected"] == {"hls": 1, "mp4": 1, "frontend_videos": 1}
    assert payload["request_count"] == 11
    assert hls_payload["request_count"] == 6
    assert mp4_payload["request_count"] == 2
    assert hls_target["video_id"] == video.pk
    assert hls_target["key_id"] == str(cast(_HlsArtifactIdentity, hls_artifact).key_id)
    assert hls_target["segment_name"] == "seg_000.ts"
    assert hls_target["key_content_length"] == hls_media.HLS_CONTENT_KEY_BYTES
    assert hls_target["playlist_x_accel_redirect"].startswith("/protected_media/")
    assert hls_target["segment_x_accel_redirect"].startswith("/protected_media/")
    assert mp4_target["video_id"] == video.pk
    assert mp4_target["legacy_stream_status"] == 302
    assert mp4_target["legacy_stream_state"] == "hls_compat_redirect"
    assert str(mp4_target["legacy_stream_location"]).endswith(
        f"/endoreg-api/media/videos/{video.pk}/hls/playlist.m3u8?type=processed"
    )
    assert mp4_target["x_accel_redirect"] is None
    assert mp4_target["lease_token_present"] is False
    frontend_payload = payload["frontend_client"]
    frontend_video = frontend_payload["videos"][0]
    assert frontend_payload["request_count"] == 3
    assert frontend_payload["simulated_hls_support"] == "hlsjs"
    assert frontend_payload["total_imported_processed_videos"] == 1
    assert frontend_payload["streaming_video_present_count"] == 1
    assert frontend_payload["missing_streaming_video_count"] == 0
    assert frontend_payload["streaming_usable_count"] == 1
    assert frontend_payload["nginx_handoff_ready_count"] == 1
    assert frontend_payload["missing_resolution_count"] == 0
    assert frontend_payload["playback_modes"]["hls"] == 1
    assert frontend_video["video_id"] == video.pk
    assert frontend_video["resolution"] == "1920x1080"
    assert frontend_video["playback_mode"] == "hls"
    assert frontend_video["hls_playlist_status"] == 200
    assert frontend_video["hls_key_status"] == 200
    assert frontend_video["hls_segment_status"] == 200
    assert frontend_video["streaming_video_present"] is True
    assert frontend_video["streaming_usable"] is True
    assert frontend_video["nginx_handoff_can_work"] is True
    assert frontend_video["hls_playlist_x_accel_redirect"].startswith(
        "/protected_media/"
    )
    assert frontend_video["hls_segment_x_accel_redirect"].startswith(
        "/protected_media/"
    )
    assert profile_path.exists()
    assert profile_path.stat().st_size > 0
    assert "function calls" in summary_path.read_text(encoding="utf-8")
    assert User.objects.count() == user_count_before
    assert MediaOperationLease.objects.count() == lease_count_before
    assert payload["media_operation_leases_before"] == lease_count_before
    assert payload["media_operation_leases_after"] == lease_count_before


def test_profile_video_streaming_frontend_falls_back_to_progressive_stream(
    streaming_profile_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    video = _create_processed_video(streaming_profile_center)
    _make_processed_streamable(video)

    stdout = StringIO()
    call_command(
        "profile_video_streaming",
        "--endpoint",
        "mp4",
        "--video-id",
        str(video.pk),
        "--iterations",
        "1",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    frontend_payload = payload["frontend_client"]
    frontend_video = frontend_payload["videos"][0]

    assert payload["selected"] == {"hls": 0, "mp4": 1, "frontend_videos": 1}
    assert payload["request_count"] == 3
    assert frontend_payload["request_count"] == 2
    assert frontend_payload["playback_modes"]["error"] == 1
    assert frontend_payload["streaming_video_present_count"] == 1
    assert frontend_payload["streaming_usable_count"] == 0
    assert frontend_payload["nginx_handoff_ready_count"] == 0
    assert frontend_video["hls_artifact_ready"] is False
    assert frontend_video["playback_mode"] == "error"
    assert frontend_video["fallback_reason"] == "hls_playlist_404"
    assert frontend_video["hls_playlist_status"] == 404
    assert frontend_video["progressive_stream_status"] == 302
    assert frontend_video["progressive_stream_state"] == "hls_compat_redirect"
    assert frontend_video["progressive_x_accel_redirect"] is None
    assert frontend_video["nginx_handoff_can_work"] is False
    assert "progressive_stream_redirect_to_hls" in frontend_video["issues"]


def test_profile_video_streaming_frontend_reports_missing_streaming_video(
    streaming_profile_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    video = _create_processed_video(streaming_profile_center)

    stdout = StringIO()
    call_command(
        "profile_video_streaming",
        "--endpoint",
        "hls",
        "--video-id",
        str(video.pk),
        "--iterations",
        "1",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    frontend_payload = payload["frontend_client"]
    frontend_video = frontend_payload["videos"][0]

    assert payload["selected"] == {"hls": 0, "mp4": 0, "frontend_videos": 1}
    assert payload["request_count"] == 2
    assert frontend_payload["request_count"] == 2
    assert frontend_payload["streaming_video_present_count"] == 0
    assert frontend_payload["missing_streaming_video_count"] == 1
    assert frontend_payload["streaming_usable_count"] == 0
    assert frontend_payload["nginx_handoff_ready_count"] == 0
    assert frontend_video["playback_mode"] == "error"
    assert frontend_video["streaming_video_present"] is False
    assert frontend_video["streaming_usable"] is False
    assert frontend_video["nginx_handoff_can_work"] is False
    assert frontend_video["hls_playlist_status"] == 404
    assert frontend_video["progressive_stream_status"] == 302
    assert frontend_video["progressive_stream_state"] == "hls_compat_redirect"
    assert "missing_streaming_video" in frontend_video["issues"]
    assert "progressive_stream_redirect_to_hls" in frontend_video["issues"]


def test_profile_video_streaming_requires_nginx_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SERVE_WITH_NGINX", raising=False)

    with pytest.raises(CommandError, match="SERVE_WITH_NGINX must be enabled"):
        call_command(
            "profile_video_streaming",
            "--json",
            stdout=StringIO(),
        )
