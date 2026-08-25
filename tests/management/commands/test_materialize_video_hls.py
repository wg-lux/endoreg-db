from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
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


def _create_raw_and_processed_video(*, center: Center) -> VideoFile:
    video = VideoFile.objects.create(
        center=center,
        video_hash="raw-and-processed-hls-command-video",
    )
    cast(Any, video.raw_file).save(
        "raw-and-processed-source-raw.mp4",
        ContentFile(b"raw source"),
        save=False,
    )
    cast(Any, video.processed_file).save(
        "raw-and-processed-source-processed.mp4",
        ContentFile(b"processed source"),
        save=False,
    )
    video.save()
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
def test_materialize_video_hls_command_defaults_to_both_required_artifacts(
    hls_command_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_command_preflight(monkeypatch)
    video = _create_raw_and_processed_video(center=hls_command_center)
    stdout = StringIO()

    call_command(
        "materialize_video_hls",
        "--json",
        stdout=stdout,
        stderr=StringIO(),
    )

    payload = json.loads(stdout.getvalue())
    assert payload["artifact_kind"] == "both"
    assert payload["selected"] == 1
    assert payload["selected_artifacts"] == 2
    assert payload["audit"]["raw"]["eligible_raw_videos"] == 1
    assert payload["audit"]["processed"]["eligible_processed_videos"] == 1
    assert payload["results"] == [
        {
            "artifact_kind": "raw",
            "status": "would_materialize",
            "video_id": video.pk,
        },
        {
            "artifact_kind": "processed",
            "status": "would_materialize",
            "video_id": video.pk,
        },
    ]


@pytest.mark.django_db
def test_materialize_video_hls_command_inline_apply_materializes_both_artifacts(
    hls_command_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_raw_and_processed_video(center=hls_command_center)
    _patch_command_preflight(monkeypatch)
    materialized_kinds: list[str] = []

    def fake_materialize_video_hls(
        video_id: int,
        *,
        artifact_kind: object,
        force: bool,
    ) -> SimpleNamespace:
        assert video_id == video.pk
        assert force is False
        selected_kind = str(artifact_kind)
        materialized_kinds.append(selected_kind)
        return SimpleNamespace(
            as_dict=lambda: {
                "video_id": video_id,
                "artifact_kind": selected_kind,
                "status": "materialized",
            }
        )

    monkeypatch.setattr(
        command_module,
        "materialize_video_hls",
        fake_materialize_video_hls,
        raising=True,
    )

    call_command(
        "materialize_video_hls",
        "--apply",
        "--inline",
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert materialized_kinds == ["raw", "processed"]


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
        "--artifact-kind",
        "processed",
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
def test_materialize_video_hls_command_dispatches_each_artifact_once(
    hls_command_center: Center,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _create_processed_video(center=hls_command_center)
    _patch_command_preflight(monkeypatch)
    from endoreg_db import tasks as task_module

    dispatched: list[tuple[object, ...]] = []

    class FakeTaskDispatcher:
        def apply_async(
            self,
            *,
            args: list[object],
            queue: str,
            routing_key: str,
        ) -> SimpleNamespace:
            assert queue == "ffmpeg_media"
            assert routing_key == queue
            dispatched.append(tuple(args))
            return SimpleNamespace(id="hls-task-1")

    monkeypatch.setattr(
        task_module,
        "video_hls_materialization",
        FakeTaskDispatcher(),
    )

    first_stdout = StringIO()
    call_command(
        "materialize_video_hls",
        "--apply",
        "--artifact-kind",
        "processed",
        "--json",
        stdout=first_stdout,
        stderr=StringIO(),
    )
    second_stdout = StringIO()
    call_command(
        "materialize_video_hls",
        "--apply",
        "--artifact-kind",
        "processed",
        "--json",
        stdout=second_stdout,
        stderr=StringIO(),
    )

    assert dispatched == [(video.pk, "processed", False)]
    assert json.loads(first_stdout.getvalue())["results"][0]["status"] == "queued"
    assert (
        json.loads(second_stdout.getvalue())["results"][0]["status"] == "already_queued"
    )
    artifact = VideoHlsArtifact.objects.get(video=video, artifact_kind="processed")
    assert artifact.status == VideoHlsArtifact.Status.QUEUED.value


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
        error_code=VideoHlsArtifact.ErrorCode.MATERIALIZATION_FAILED.value,
    )

    stdout = StringIO()
    call_command(
        "materialize_video_hls",
        "--artifact-kind",
        "processed",
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
            "queued": 0,
            "materializing": 0,
            "validated": 0,
            "ready": 1,
            "superseded": 0,
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
            "--artifact-kind",
            "processed",
            "--json",
            stdout=stdout,
            stderr=StringIO(),
        )

    payload = json.loads(stdout.getvalue())
    assert payload["preflight"]["ffmpeg_available"] is False
    assert payload["preflight"]["errors"] == ["ffmpeg executable is not available"]
    assert payload["results"] == []
    assert not VideoHlsArtifact.objects.filter(video=video).exists()
