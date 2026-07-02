from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from endoreg_db.management.commands import (
    migrate_video_streamable_storage as command_module,
)
from endoreg_db.models import Center, VideoFile

pytestmark = pytest.mark.django_db


def test_migrate_video_streamable_storage_regenerate_forces_sync(
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
    captured: list[dict[str, object]] = []

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
        return []

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
            "force": True,
        }
    ]
    assert (
        f"video={video.pk} regenerated: 1 streamable artifact(s)" in output.getvalue()
    )
    assert "regenerated=1" in output.getvalue()
