from __future__ import annotations

import hashlib
import re
from contextlib import AbstractContextManager
from pathlib import Path

from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.filesystem.file_operations import advisory_file_lock

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _report_lock_root() -> Path:
    return (
        path_utils.EndoregPathsModel.from_environment().staging_migration
        / "report_locks_v2"
    )


def report_source_lock(path: Path) -> AbstractContextManager[None]:
    resolved_reference = str(Path(path).resolve()).encode("utf-8")
    path_digest = hashlib.sha256(resolved_reference).hexdigest()
    return advisory_file_lock(
        lock_path=_report_lock_root() / "source" / f"{path_digest}.lock",
    )


def report_content_hash_lock(
    file_hash: str,
    lock_root: Path | None = None,
) -> AbstractContextManager[None]:
    normalized_hash = file_hash.strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized_hash):
        raise ValueError("file_hash must be a lowercase SHA-256 hex digest")
    content_root = lock_root or (_report_lock_root() / "content")
    return advisory_file_lock(
        lock_path=content_root / f"{normalized_hash}.lock",
    )
