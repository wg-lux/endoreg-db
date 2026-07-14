from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command

from endoreg_db.management.commands import materialize_video_hls as command_module
from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.services import hls_media
from endoreg_db.utils.paths import EndoregPathsModel
from tests.helpers.hls import FakeHlsOutputRecorder


@pytest.fixture
def hls_command_center() -> Center:
    return Center.objects.create(
        name="hls-command-center",
        display_name="HLS Command Center",
    )


def _create_processed_video(
    *,
    center: Center,
    payload: bytes = b"hls command source",
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"hls-command-video-{payload.hex()}",
    )
    cast(Any, video.processed_file).save(
        "hls-command-source.mp4",
        ContentFile(payload),
        save=True,
    )
    return video


def _create_raw_video(
    *,
    center: Center,
    payload: bytes = b"raw hls command source",
) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash=f"raw-hls-command-video-{payload.hex()}",
    )
    cast(Any, video.raw_file).save(
        "raw-hls-command-source.mp4",
        ContentFile(payload),
        save=True,
    )
    return video


def _patch_command_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        command_module.ffmpeg_wrapper,
        "resolve_ffmpeg_executable",
        lambda: "/usr/bin/ffmpeg",
    )


@pytest.mark.django_db
def test_materialize_video_hls_command_selects_raw_artifacts(
    hls_command_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_command_preflight(monkeypatch)
    _create_raw_video(center=hls_command_center)
    stdout = StringIO()

    call_command(
        "materialize_video_hls",
        "--artifact-kind",
        "raw",
        "--json",
        stdout=stdout,
        stderr=StringIO(),
    )

    payload = json.loads(stdout.getvalue())
    assert payload["artifact_kind"] == "raw"
    assert payload["selected"] == 1
    assert payload["audit"]["eligible_raw_videos"] == 1
    assert payload["results"][0]["status"] == "would_materialize"


@pytest.mark.django_db
def test_materialize_video_hls_command_inline_apply_materializes_artifact(
    hls_command_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_processed_video(center=hls_command_center)
    fake_hls = FakeHlsOutputRecorder()
    _patch_command_preflight(monkeypatch)
    monkeypatch.setattr(hls_media, "_run_ffmpeg_hls", fake_hls.run)

    stdout = StringIO()
    call_command(
        "materialize_video_hls",
        "--video-id",
        str(video.pk),
        "--apply",
        "--inline",
        "--json",
        stdout=stdout,
        stderr=StringIO(),
    )

    payload = json.loads(stdout.getvalue())
    assert payload["apply"] is True
    assert payload["inline"] is True
    assert payload["artifact_kind"] == "processed"
    assert payload["selected"] == 1
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["video_id"] == video.pk
    assert result["status"] == "materialized"

    artifact = VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")
    assert artifact.status == VideoHlsArtifact.Status.READY.value
    assert artifact.segment_count == 1

    paths = EndoregPathsModel.from_environment()
    playlist_path = Path(paths.storage / artifact.playlist_relative_path)
    segment_dir = Path(paths.storage / artifact.segment_directory_relative_path)
    assert playlist_path.exists()
    assert (segment_dir / "seg_000.ts").exists()
    assert fake_hls.source_payloads == [b"hls command source"]
    assert not (
        paths.transcoding / "hls_output" / str(video.pk) / str(artifact.key_id)
    ).exists()


@pytest.mark.django_db
def test_materialize_video_hls_command_dry_run_reports_bulk_audit(
    hls_command_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_command_preflight(monkeypatch)
    VideoFile.objects.create(
        center=hls_command_center,
        video_hash="hls-command-without-processed-file",
    )
    ready_video = _create_processed_video(
        center=hls_command_center,
        payload=b"ready hls command source",
    )
    failed_video = _create_processed_video(
        center=hls_command_center,
        payload=b"failed hls command source",
    )
    _create_processed_video(
        center=hls_command_center,
        payload=b"missing hls command source",
    )
    VideoHlsArtifact.objects.create(
        video=ready_video,
        artifact_kind=VideoHlsArtifact.ArtifactKind.PROCESSED.value,
        status=VideoHlsArtifact.Status.READY.value,
    )
    VideoHlsArtifact.objects.create(
        video=failed_video,
        artifact_kind=VideoHlsArtifact.ArtifactKind.PROCESSED.value,
        status=VideoHlsArtifact.Status.FAILED.value,
    )

    stdout = StringIO()
    call_command(
        "materialize_video_hls",
        "--json",
        stdout=stdout,
        stderr=StringIO(),
    )

    payload = json.loads(stdout.getvalue())
    assert payload["apply"] is False
    assert payload["artifact_kind"] == "processed"
    assert payload["selected"] == 3
    assert payload["audit"] == {
        "total_videos": 4,
        "eligible_processed_videos": 3,
        "videos_without_processed_file": 1,
        "hls_artifacts": {
            "materializing": 0,
            "ready": 1,
            "failed": 1,
        },
        "missing_hls_artifacts": 1,
    }
    assert payload["preflight"]["master_key_available"] is True
    assert payload["preflight"]["ffmpeg_available"] is True
    assert [result["status"] for result in payload["results"]] == [
        "would_materialize",
        "would_materialize",
        "would_materialize",
    ]


@pytest.mark.django_db
def test_materialize_video_hls_command_apply_fails_closed_on_preflight_error(
    hls_command_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_processed_video(center=hls_command_center)
    monkeypatch.setattr(command_module, "load_master_key", lambda: b"0" * 32)
    monkeypatch.setattr(
        command_module.ffmpeg_wrapper,
        "resolve_ffmpeg_executable",
        lambda: None,
    )

    stdout = StringIO()
    with pytest.raises(CommandError, match="ffmpeg executable is not available"):
        call_command(
            "materialize_video_hls",
            "--video-id",
            str(video.pk),
            "--apply",
            "--inline",
            "--json",
            stdout=stdout,
            stderr=StringIO(),
        )

    payload = json.loads(stdout.getvalue())
    assert payload["preflight"]["ffmpeg_available"] is False
    assert payload["preflight"]["errors"] == ["ffmpeg executable is not available"]
    assert payload["results"] == []
    assert not VideoHlsArtifact.objects.filter(video=video).exists()
