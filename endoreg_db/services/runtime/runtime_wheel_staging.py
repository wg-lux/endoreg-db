from __future__ import annotations

import logging
import stat
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import InvalidWheelFilename, parse_wheel_filename

from endoreg_db.schemas.runtime_wheel_staging import (
    RuntimeWheelStagingCandidate,
    RuntimeWheelStagingCleanupResult,
)
from endoreg_db.utils.file_operations import safe_unlink_file
from endoreg_db.utils.structured_logging import emit_structured_event, path_reference


logger = logging.getLogger(__name__)

LX_ANNOTATE_DISTRIBUTION = "lx-annotate"
DEFAULT_MAX_RUNTIME_ROOT_ENTRIES = 1024


@dataclass(frozen=True)
class _CandidateIdentity:
    path: Path
    device: int
    inode: int
    size_bytes: int
    mtime_ns: int


def _validated_runtime_root(runtime_root: Path) -> Path:
    root = Path(runtime_root)
    if not root.is_absolute():
        raise ValueError("Runtime root must be an absolute path")
    if root.is_symlink():
        raise ValueError("Runtime root must not be a symbolic link")
    resolved = root.resolve(strict=True)
    if resolved != root:
        raise ValueError("Runtime root must be provided as its canonical path")
    if not root.is_dir():
        raise ValueError("Runtime root must be a directory")
    return root


def _validated_keep_names(keep_names: frozenset[str]) -> frozenset[str]:
    for name in keep_names:
        if not name or Path(name).name != name:
            raise ValueError("Wheel keep names must be non-empty basenames")
    return keep_names


def _is_lx_annotate_wheel_name(name: str) -> bool:
    if not name.endswith(".whl"):
        return False
    if not name.startswith(("lx_annotate-", "lx-annotate-")):
        return False
    try:
        distribution, _, _, _ = parse_wheel_filename(name)
    except InvalidWheelFilename as exc:
        raise ValueError(
            f"Ambiguous LX-Annotate wheel staging filename: {name}"
        ) from exc
    if str(distribution) != LX_ANNOTATE_DISTRIBUTION:
        raise ValueError(f"Unexpected wheel distribution in runtime staging: {name}")
    return True


def _inventory_candidates(
    *,
    runtime_root: Path,
    keep_names: frozenset[str],
    max_entries: int,
) -> tuple[int, tuple[_CandidateIdentity, ...]]:
    if max_entries <= 0:
        raise ValueError("max_entries must be positive")

    identities: list[_CandidateIdentity] = []
    scanned_entries = 0
    for entry in runtime_root.iterdir():
        scanned_entries += 1
        if scanned_entries > max_entries:
            raise ValueError(
                f"Runtime root contains more than the bounded {max_entries} entries"
            )
        if entry.name in keep_names or not _is_lx_annotate_wheel_name(entry.name):
            continue
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                f"LX-Annotate wheel staging candidate is not a regular file: {entry.name}"
            )
        identities.append(
            _CandidateIdentity(
                path=entry,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                size_bytes=metadata.st_size,
                mtime_ns=metadata.st_mtime_ns,
            )
        )
    identities.sort(key=lambda item: item.path.name)
    return scanned_entries, tuple(identities)


def _assert_identity_unchanged(candidate: _CandidateIdentity) -> None:
    current = candidate.path.lstat()
    current_identity = (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )
    expected_identity = (
        candidate.device,
        candidate.inode,
        candidate.size_bytes,
        candidate.mtime_ns,
    )
    if not stat.S_ISREG(current.st_mode) or current_identity != expected_identity:
        raise RuntimeError(
            f"Runtime wheel staging candidate changed during cleanup: {candidate.path.name}"
        )


def reap_runtime_wheel_staging(
    *,
    runtime_root: Path,
    apply: bool = False,
    keep_names: frozenset[str] = frozenset(),
    max_entries: int = DEFAULT_MAX_RUNTIME_ROOT_ENTRIES,
) -> RuntimeWheelStagingCleanupResult:
    """Inventory or remove obsolete top-level LX-Annotate wheel staging files."""
    root = _validated_runtime_root(runtime_root)
    preserved_names = _validated_keep_names(keep_names)
    scanned_entries, identities = _inventory_candidates(
        runtime_root=root,
        keep_names=preserved_names,
        max_entries=max_entries,
    )
    candidates = tuple(
        RuntimeWheelStagingCandidate(
            name=identity.path.name,
            size_bytes=identity.size_bytes,
        )
        for identity in identities
    )
    candidate_bytes = sum(item.size_bytes for item in candidates)

    emit_structured_event(
        logger,
        "runtime_wheel_staging.inventory",
        mode="apply" if apply else "dry_run",
        runtime_root=path_reference(root),
        scanned_entries=scanned_entries,
        candidate_count=len(candidates),
        candidate_bytes=candidate_bytes,
        preserved_count=len(preserved_names),
    )

    removed_count = 0
    removed_bytes = 0
    if apply:
        for identity in identities:
            _assert_identity_unchanged(identity)
            safe_unlink_file(identity.path, missing_ok=False)
            removed_count += 1
            removed_bytes += identity.size_bytes

    result = RuntimeWheelStagingCleanupResult(
        mode="apply" if apply else "dry_run",
        scanned_entries=scanned_entries,
        candidate_count=len(candidates),
        candidate_bytes=candidate_bytes,
        removed_count=removed_count,
        removed_bytes=removed_bytes,
        candidates=candidates,
    )
    emit_structured_event(
        logger,
        "runtime_wheel_staging.complete",
        **result.model_dump(mode="json", exclude={"candidates"}),
    )
    return result


__all__ = [
    "DEFAULT_MAX_RUNTIME_ROOT_ENTRIES",
    "RuntimeWheelStagingCandidate",
    "RuntimeWheelStagingCleanupResult",
    "reap_runtime_wheel_staging",
]
