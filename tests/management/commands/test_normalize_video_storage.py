from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command

from endoreg_db.models import Center, VideoFile, VideoHlsArtifact
from endoreg_db.services.video_storage_normalization import (
    VideoStorageCapacityReport,
    VideoStorageNormalizationError,
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
    assert payload["inventory_cursor"] == {
        "after_video_id": 0,
        "next_after_video_id": video.pk,
        "batch_limit": 100,
    }
    video.refresh_from_db()
    assert video.raw_file.name == raw_name
    assert video.processed_file.name == processed_name


def test_normalize_video_storage_applies_cursor_and_limit_before_inventory() -> None:
    first = _video_with_storage()
    second = VideoFile.objects.create(
        center=first.center,
        video_hash="storage-normalization-video-second",
        fps=25.0,
        duration=10.0,
        frame_count=250,
    )
    third = VideoFile.objects.create(
        center=first.center,
        video_hash="storage-normalization-video-third",
        fps=25.0,
        duration=10.0,
        frame_count=250,
    )
    stdout = StringIO()

    call_command(
        "normalize_video_storage",
        "--after-video-id",
        str(first.pk),
        "--limit",
        "1",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["selected"] == 1
    assert payload["results"][0]["before"]["video_id"] == second.pk
    assert payload["inventory_cursor"] == {
        "after_video_id": first.pk,
        "next_after_video_id": second.pk,
        "batch_limit": 1,
    }
    assert third.pk > payload["inventory_cursor"]["next_after_video_id"]


def test_normalize_video_storage_rejects_unbounded_limit() -> None:
    with pytest.raises(CommandError, match="--limit must not exceed 1000"):
        call_command("normalize_video_storage", "--limit", "1001", "--json")


def test_inventory_rejects_unknown_hls_artifact_kind() -> None:
    video = _video_with_storage()
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind="unsupported",
        status=VideoHlsArtifact.Status.FAILED,
        error_code=VideoHlsArtifact.ErrorCode.INCONSISTENT_ARTIFACT,
    )

    with pytest.raises(
        VideoStorageNormalizationError,
        match=f"Unsupported HLS artifact kind for video {video.pk}",
    ):
        call_command(
            "normalize_video_storage",
            "--video-id",
            str(video.pk),
            "--json",
        )


def test_inventory_marks_hls_artifact_above_entry_limit_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = _video_with_storage()
    VideoHlsArtifact.objects.create(
        video=video,
        artifact_kind=VideoHlsArtifact.ArtifactKind.PROCESSED,
        status=VideoHlsArtifact.Status.FAILED,
        error_code=VideoHlsArtifact.ErrorCode.INCONSISTENT_ARTIFACT,
        segment_directory_relative_path="hls/test-segments",
    )
    segment_directory = tmp_path / "test-segments"
    segment_directory.mkdir()
    (segment_directory / "segment-00000.ts").write_bytes(b"first")
    (segment_directory / "segment-00001.ts").write_bytes(b"second")

    import endoreg_db.services.video_storage.inventory as inventory_module
    from endoreg_db.utils import paths as path_utils

    def resolve_inventory_path(relative_path: str) -> Path | None:
        if relative_path == "hls/test-segments":
            return segment_directory
        return None

    monkeypatch.setattr(
        path_utils,
        "resolve_existing_protected_media_path",
        resolve_inventory_path,
    )
    monkeypatch.setattr(
        inventory_module,
        "MAX_HLS_INVENTORY_ENTRIES_PER_ARTIFACT",
        1,
    )

    stdout = StringIO()
    call_command(
        "normalize_video_storage",
        "--video-id",
        str(video.pk),
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    before = payload["results"][0]["before"]
    assert payload["incomplete_hls_inventory_artifacts"] == 1
    assert payload["reconciliation_error_videos"] == 1
    assert before["incomplete_hls_inventory_artifacts"] == 1
    assert before["reconciled"] is False
    assert before["reclaimable_raw_bytes"] == 0

    monkeypatch.setattr(
        "endoreg_db.management.commands.normalize_video_storage.video_storage_destructive_migration_enabled",
        lambda: True,
    )
    with pytest.raises(CommandError, match="reconciliation failed before mutation"):
        call_command(
            "normalize_video_storage",
            "--video-id",
            str(video.pk),
            "--apply",
        )


def test_inventory_cursor_does_not_advance_after_reconciliation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video_with_storage()
    import endoreg_db.management.commands.normalize_video_storage as command_module

    def simulate_reconciliation_failure(
        _command: object,
        **_kwargs: object,
    ) -> tuple[list[dict[str, Any]], bool]:
        return [], True

    monkeypatch.setattr(
        command_module.Command,
        "_process_rows",
        simulate_reconciliation_failure,
    )
    stdout = StringIO()

    with pytest.raises(CommandError, match="reconciliation failed after a batch"):
        call_command(
            "normalize_video_storage",
            "--video-id",
            str(video.pk),
            "--json",
            stdout=stdout,
        )

    payload = json.loads(stdout.getvalue())
    assert payload["inventory_cursor"]["next_after_video_id"] is None


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
