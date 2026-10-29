"""Publish validated package artifacts through the host filesystem boundary."""

from pathlib import Path
from uuid import uuid4

from lx_dtypes.packages import (
    export_package_files,
    package_archive_bytes,
    read_package_archive,
)

from endoreg_db.utils import file_operations
from endoreg_db.utils.paths import ensure_within_runtime_root, get_runtime_paths


def _destination(root: Path, name: str) -> Path:
    if not name or name in {".", ".."} or Path(name).name != name or "\\" in name:
        raise ValueError("Artifact name must be a single path component")
    destination = root / name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    return ensure_within_runtime_root(destination)


def _publish(files: dict[str, bytes], name: str) -> Path:
    paths = get_runtime_paths()
    root = ensure_within_runtime_root(paths.terminology)
    with file_operations.advisory_file_lock(
        lock_path=paths.runtime_root / ".terminology-deployment.lock"
    ):
        destination = _destination(root, name)
        staging = file_operations.ensure_directory(root / f".staging-{uuid4().hex}")
        try:
            for relative, content in files.items():
                file_operations.atomic_create_file(
                    destination=staging / relative,
                    content=(content,),
                    file_mode=0o640,
                    dir_mode=0o750,
                )
            file_operations.atomic_move_path(source=staging, destination=destination)
        finally:
            file_operations.safe_rmtree(staging)
    return destination / "config.yaml"


def import_terminology_package(archive: Path, *, name: str) -> Path:
    """Validate a ZIP and publish a new folder without registry activation."""
    return _publish(read_package_archive(archive), name)


def build_terminology_package(config_path: Path, *, name: str) -> Path:
    """Build a portable folder containing the selected package and dependencies."""
    return _publish(export_package_files(config_path), name)


def export_terminology_package(config_path: Path, *, name: str) -> Path:
    """Create a validated ZIP in the configured runtime export directory."""
    content = package_archive_bytes(config_path)
    destination = _destination(get_runtime_paths().export_dir, name)
    return file_operations.atomic_create_file(
        destination=destination, content=(content,), file_mode=0o640, dir_mode=0o750
    )
