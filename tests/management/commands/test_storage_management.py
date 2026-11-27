from __future__ import annotations

from collections.abc import Callable
from io import StringIO
from pathlib import Path
from typing import cast

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from endoreg_db.management.commands import storage_management as command_module
from endoreg_db.models.media.video.video_file import VideoFile
from lx_dtypes.models.contracts.management_command import (
    StorageManagementCommandOptionsPayload,
    StorageManagementInfoPayload,
)

pytestmark = pytest.mark.no_db


def _storage_info(usage: float) -> StorageManagementInfoPayload:
    return StorageManagementInfoPayload(
        total_gb=100,
        used_gb=usage,
        free_gb=100 - usage,
        usage_percent=usage,
        project_storage_gb=10,
        critical=usage >= 95,
        warning=usage >= 85,
    )


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("usage", "flags"),
    [
        (95, []),
        (99, []),
        (20, ["--force"]),
        (20, ["--cleanup-frames"]),
        (20, ["--cleanup-old-videos", "--max-age-days", "0"]),
        (20, ["--cleanup-uploads"]),
        (20, ["--cleanup-logs"]),
        (85, ["--emergency-threshold", "85"]),
    ],
)
def test_cleanup_requests_fail_closed(
    monkeypatch: pytest.MonkeyPatch, usage: float, flags: list[str], dry_run: bool
) -> None:
    command = command_module.Command(stdout=StringIO())
    monkeypatch.setattr(command, "get_storage_info", lambda: _storage_info(usage))
    args = [*flags, *(["--dry-run"] if dry_run else [])]

    with pytest.raises(
        CommandError, match="Legacy storage_management cleanup is disabled"
    ):
        call_command(command, *args)


@pytest.mark.parametrize(
    "method_name",
    [
        "emergency_cleanup",
        "maintenance_cleanup",
        "cleanup_extracted_frames",
        "cleanup_upload_cache",
        "cleanup_old_logs",
        "cleanup_temp_files",
        "cleanup_old_processed_videos",
        "cleanup_all_extracted_frames",
        "cleanup_all_uploads",
        "cleanup_all_processed_videos",
        "_cleanup_processed_video_file",
    ],
)
def test_direct_cleanup_helpers_reject_without_touching_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    command = command_module.Command(stdout=StringIO())
    command.dry_run = False
    command.max_age_days = 0
    # Existing master, frame, upload and another process's temporary file remain intact.
    paths = [
        tmp_path / name
        for name in ("master.mp4", "frame.jpg", "upload.mp4", "tmp-other")
    ]
    for path in paths:
        path.write_bytes(b"retained")

    def reject_traversal(_path: Path) -> int:
        pytest.fail("cleanup must reject before scanning files")

    monkeypatch.setattr(command, "get_directory_size", reject_traversal)
    args: tuple[object, ...] = ()
    if method_name == "cleanup_old_processed_videos":
        args = (0,)
    elif method_name == "_cleanup_processed_video_file":
        args = (VideoFile(),)
    elif method_name == "maintenance_cleanup":
        args = (
            StorageManagementCommandOptionsPayload(
                dry_run=False,
                force=False,
                cleanup_frames=True,
                cleanup_old_videos=False,
                cleanup_uploads=False,
                cleanup_logs=False,
                max_age_days=0,
                emergency_threshold=95.0,
            ),
        )
    method = cast(Callable[..., object], getattr(command, method_name))

    with pytest.raises(
        CommandError, match="Legacy storage_management cleanup is disabled"
    ):
        method(*args)

    assert [path.read_bytes() for path in paths] == [b"retained"] * len(paths)


def test_storage_capacity_uses_canonical_media_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = command_module.Command(stdout=StringIO())
    storage_root = tmp_path / "separate-media-mount"
    checked: list[Path] = []

    def disk_usage(path: Path) -> tuple[int, int, int]:
        checked.append(path)
        return (100, 96, 4)

    def directory_size(_path: Path) -> int:
        return 10

    monkeypatch.setattr(command, "get_storage_root", lambda: storage_root)
    monkeypatch.setattr(command, "get_directory_size", directory_size)
    monkeypatch.setattr(command_module.shutil, "disk_usage", disk_usage)

    assert command.get_storage_info().usage_percent == 96
    assert checked == [storage_root]


def test_unavailable_media_filesystem_fails_without_root_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = command_module.Command(stdout=StringIO())
    storage_root = tmp_path / "unavailable-media-mount"
    checked: list[Path] = []

    def disk_usage(path: Path) -> tuple[int, int, int]:
        checked.append(path)
        raise FileNotFoundError("media storage unavailable")

    monkeypatch.setattr(command, "get_storage_root", lambda: storage_root)
    monkeypatch.setattr(command_module.shutil, "disk_usage", disk_usage)

    with pytest.raises(CommandError, match="media storage unavailable"):
        call_command(command)
    assert checked == [storage_root]


def test_read_only_status_remains_available(monkeypatch: pytest.MonkeyPatch) -> None:
    output = StringIO()
    command = command_module.Command(stdout=output)
    monkeypatch.setattr(command, "get_storage_info", lambda: _storage_info(20))

    call_command(command)

    assert "Usage:       20.0%" in output.getvalue()
