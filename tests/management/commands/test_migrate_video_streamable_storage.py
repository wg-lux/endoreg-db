from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from endoreg_db.management.commands import (
    migrate_video_streamable_storage as command_module,
)
from endoreg_db.models import Center, VideoFile

pytestmark = pytest.mark.django_db


def test_migrate_video_streamable_storage_regenerate_forces_hls_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(
        name="streamable-regenerate-center",
        display_name="Streamable Regenerate Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="streamable-regenerate-video",
    )
    video.processed_file.name = "processed_videos_final/source.mp4"
    video.processed_streamable_relative_path = "streamable_videos/processed/source.mp4"
    video.save(update_fields=["processed_file", "processed_streamable_relative_path"])
    captured: list[dict[str, object]] = []
    materialized: list[dict[str, object]] = []

    def fake_selected_streamable_artifact_count(
        selected_video: VideoFile,
        *,
        include_raw: bool,
        include_processed: bool,
    ) -> int:
        assert selected_video.pk == video.pk
        assert include_raw is False
        assert include_processed is True
        return 1

    def fake_sync_video_streamable_artifacts(
        selected_video: VideoFile,
        *,
        include_raw: bool,
        include_processed: bool,
        save: bool,
        force: bool,
    ) -> list[str]:
        assert selected_video.pk == video.pk
        captured.append(
            {
                "include_raw": include_raw,
                "include_processed": include_processed,
                "save": save,
                "force": force,
            }
        )
        return ["processed_streamable_relative_path", "storage_mode"]

    def fake_materialize_video_hls(
        video_id: int,
        *,
        artifact_kind: object,
        force: bool,
    ) -> object:
        materialized.append(
            {
                "video_id": video_id,
                "artifact_kind": artifact_kind,
                "force": force,
            }
        )
        return object()

    monkeypatch.setattr(
        command_module,
        "_selected_streamable_artifact_count",
        fake_selected_streamable_artifact_count,
        raising=True,
    )
    monkeypatch.setattr(
        command_module,
        "sync_video_streamable_artifacts",
        fake_sync_video_streamable_artifacts,
        raising=True,
    )
    monkeypatch.setattr(
        command_module,
        "materialize_video_hls",
        fake_materialize_video_hls,
        raising=True,
    )

    output = StringIO()
    call_command(
        "migrate_video_streamable_storage",
        "--video-id",
        str(video.pk),
        "--processed-only",
        "--regenerate",
        stdout=output,
    )

    assert captured == [
        {
            "include_raw": False,
            "include_processed": True,
            "save": True,
            "force": False,
        }
    ]
    assert materialized == [
        {
            "video_id": video.pk,
            "artifact_kind": "processed",
            "force": True,
        }
    ]
    assert (
        f"video={video.pk} updated: processed_streamable_relative_path"
        in output.getvalue()
    )
    assert "hls_materialized=1" in output.getvalue()
