from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from endoreg_db.utils.file_operations import ensure_directory, set_path_mode

FRAME_CACHE_DIR_MODE = 0o750
FRAME_STAGING_DIR_MODE = 0o700
FRAME_FILE_MODE = 0o640


def ensure_frame_cache_dir(path: Path) -> Path:
    return ensure_directory(Path(path), dir_mode=FRAME_CACHE_DIR_MODE)


def ensure_frame_staging_dir(path: Path) -> Path:
    return ensure_directory(Path(path), dir_mode=FRAME_STAGING_DIR_MODE)


def apply_frame_cache_dir_mode(path: Path) -> None:
    target = Path(path)
    if target.exists():
        set_path_mode(target, FRAME_CACHE_DIR_MODE)


def apply_frame_file_mode(path: Path) -> None:
    target = Path(path)
    if target.exists():
        set_path_mode(target, FRAME_FILE_MODE)


def apply_frame_file_modes(paths: Iterable[Path]) -> None:
    for path in paths:
        apply_frame_file_mode(path)
