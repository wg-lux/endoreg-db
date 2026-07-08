from __future__ import annotations

from io import StringIO
from typing import Any, cast

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.services import hls_media
from tests.helpers.hls import FakeHlsOutputRecorder


@pytest.fixture
def hls_readiness_center() -> Center:
    return Center.objects.create(
        name="hls-readiness-center",
        display_name="HLS Readiness Center",
    )


def _create_processed_video(
    *,
    center: Center,
    payload: bytes = b"hls readiness source",
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"hls-readiness-video-{payload.hex()}",
    )
    cast(Any, video.processed_file).save(
        "hls-readiness-source.mp4",
        ContentFile(payload),
        save=True,
    )
    return video


def _materialize_fake_hls(
    video: VideoFile,
    monkeypatch: pytest.MonkeyPatch,
) -> VideoHlsArtifact:
    fake_hls = FakeHlsOutputRecorder()
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)
    hls_media.materialize_video_hls(video.pk, artifact_kind="processed")
    return VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")


def _enable_nginx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVE_WITH_NGINX", "true")
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")


@pytest.mark.django_db
def test_check_production_hls_readiness_succeeds_for_ready_hls_video(
    hls_readiness_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_nginx(monkeypatch)
    video = _create_processed_video(center=hls_readiness_center)
    _materialize_fake_hls(video, monkeypatch)

    stdout = StringIO()
    call_command(
        "check_production_hls_readiness",
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert "Pruefung abgeschlossen: 1 Videos valide, 0 fehlerhaft, 0 Blocker." in output


@pytest.mark.django_db
def test_check_production_hls_readiness_fails_on_legacy_streamable_path(
    hls_readiness_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_nginx(monkeypatch)
    video = _create_processed_video(center=hls_readiness_center)
    _materialize_fake_hls(video, monkeypatch)
    video.processed_streamable_relative_path = "streamable_videos/processed/legacy.mp4"
    video.save(update_fields=["processed_streamable_relative_path"])

    stdout = StringIO()
    with pytest.raises(SystemExit) as exc_info:
        call_command(
            "check_production_hls_readiness",
            stdout=stdout,
            stderr=StringIO(),
        )

    assert exc_info.value.code == 1
    output = stdout.getvalue()
    assert "[legacy] streamable MP4 path residues: 1" in output
    assert "migrate_video_streamable_storage" in output
