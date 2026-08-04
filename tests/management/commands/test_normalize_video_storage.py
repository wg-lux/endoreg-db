from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command

from endoreg_db.models import Center, VideoFile
from endoreg_db.services.video_storage_normalization import (
    VideoStorageCapacityReport,
)

pytestmark = pytest.mark.django_db


def _video_with_storage() -> VideoFile:
    center = Center.objects.create(
        name="storage-normalization-center",
        display_name="Storage Normalization Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="storage-normalization-video",
        fps=25.0,
        duration=10.0,
        frame_count=250,
    )
    cast(Any, video.raw_file).save(
        "storage-normalization-raw.mp4",
        ContentFile(b"raw-video"),
        save=True,
    )
    cast(Any, video.processed_file).save(
        "storage-normalization-processed.mp4",
        ContentFile(b"processed-video"),
        save=True,
    )
    return video


def test_normalize_video_storage_defaults_to_inventory_only() -> None:
    video = _video_with_storage()
    raw_name = video.raw_file.name
    processed_name = video.processed_file.name
    stdout = StringIO()

    call_command(
        "normalize_video_storage",
        "--video-id",
        str(video.pk),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["apply"] is False
    assert payload["selected"] == 1
    assert payload["raw_bytes"] > 0
    assert payload["processed_bytes"] > 0
    assert payload["reclaimed_bytes"] == 0
    assert payload["pending_videos"] == 1
    assert payload["normalized_videos"] == 0
    assert payload["reconciliation_error_videos"] == 0
    assert payload["capacity"]["status"] in {"ok", "warning", "stop"}
    assert payload["profile"]["max_width"] == 4096
    assert payload["profile"]["annotation_max_fps"] == 50.0
    video.refresh_from_db()
    assert video.raw_file.name == raw_name
    assert video.processed_file.name == processed_name


def test_normalize_video_storage_apply_fails_while_gate_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video_with_storage()
    import endoreg_db.management.commands.normalize_video_storage as command_module

    monkeypatch.setattr(
        command_module,
        "video_storage_destructive_migration_enabled",
        lambda: False,
    )

    with pytest.raises(CommandError, match="Destructive video migration is disabled"):
        call_command(
            "normalize_video_storage",
            "--video-id",
            str(video.pk),
            "--apply",
        )


def test_normalize_video_storage_apply_fails_on_missing_database_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video_with_storage()
    import endoreg_db.management.commands.normalize_video_storage as command_module

    processed_name = str(video.processed_file.name)
    video.processed_file.storage.delete(processed_name)
    monkeypatch.setattr(
        command_module,
        "video_storage_destructive_migration_enabled",
        lambda: True,
    )
    transcode_called = False

    def fail_if_called(*args: object, **kwargs: object) -> object:
        nonlocal transcode_called
        transcode_called = True
        raise AssertionError("transcode must not run after reconciliation failure")

    monkeypatch.setattr(
        command_module,
        "transcode_processed_video_for_storage_pressure",
        fail_if_called,
    )

    with pytest.raises(CommandError, match="reconciliation failed before mutation"):
        call_command(
            "normalize_video_storage",
            "--video-id",
            str(video.pk),
            "--apply",
        )

    assert transcode_called is False


def test_normalize_video_storage_apply_fails_at_capacity_hard_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video_with_storage()
    import endoreg_db.management.commands.normalize_video_storage as command_module

    monkeypatch.setattr(
        command_module,
        "video_storage_destructive_migration_enabled",
        lambda: True,
    )

    def hard_stop_capacity(
        *,
        storage_root: Path,
        projected_temporary_bytes: int = 0,
    ) -> VideoStorageCapacityReport:
        del storage_root, projected_temporary_bytes
        return VideoStorageCapacityReport(
            total_bytes=10_000,
            used_bytes=9_500,
            free_bytes=500,
            projected_temporary_bytes=100,
            warning_free_bytes=2_000,
            stop_free_bytes=1_000,
            status="stop",
        )

    monkeypatch.setattr(
        command_module,
        "video_storage_capacity",
        hard_stop_capacity,
    )
    transcode_called = False

    def fail_if_called(*args: object, **kwargs: object) -> object:
        nonlocal transcode_called
        transcode_called = True
        raise AssertionError("transcode must not run at the hard-stop threshold")

    monkeypatch.setattr(
        command_module,
        "transcode_processed_video_for_storage_pressure",
        fail_if_called,
    )

    with pytest.raises(CommandError, match="Storage hard-stop threshold reached"):
        call_command(
            "normalize_video_storage",
            "--video-id",
            str(video.pk),
            "--apply",
        )

    assert transcode_called is False
