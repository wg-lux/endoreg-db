from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from django.db import transaction

if TYPE_CHECKING:
    from endoreg_db.import_files.context.import_context import ImportContext

from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.file_operations import safe_unlink_file

logger = logging.getLogger(__name__)


def staging_cleanup_roots() -> tuple[Path, ...]:
    paths = get_runtime_paths()
    return (
        paths.transcoding,
        paths.import_video,
        paths.import_report,
        paths.import_preanonymized,
        paths.import_anonymized_video,
        paths.import_anonymized_report,
        paths.sensitive_video,
        paths.sensitive_report,
        paths.upload_api,
        paths.upload_watcher,
        paths.upload_preanonymized,
    )


def is_safe_staging_path(
    path: Path | None,
    *,
    allowed_roots: Iterable[Path] | None = None,
) -> bool:
    """
    Return True only for paths inside approved plaintext staging roots.

    This is intentionally Path-only. Canonical FileField payloads must be
    deleted through Django storage helpers, never by filesystem cleanup.
    """
    if path is None:
        return False
    target = Path(path)
    roots = tuple(staging_cleanup_roots() if allowed_roots is None else allowed_roots)
    return any(target.resolve().is_relative_to(root.resolve()) for root in roots)


def safe_cleanup_staging_file(
    path: Path | None,
    *,
    label: str,
    allowed_roots: Iterable[Path] | None = None,
    missing_ok: bool = True,
) -> bool:
    if path is None:
        return False

    target = Path(path)
    roots = tuple(staging_cleanup_roots() if allowed_roots is None else allowed_roots)
    payload = {
        "operation": "cleanup_staging_file",
        "label": label,
        "path": str(target),
    }

    if not target.exists():
        logger.info("%s", json.dumps({**payload, "status": "missing"}))
        return False
    if not target.is_file() or target.is_symlink():
        logger.warning("%s", json.dumps({**payload, "status": "rejected_not_file"}))
        return False
    if not is_safe_staging_path(target, allowed_roots=roots):
        logger.warning(
            "%s", json.dumps({**payload, "status": "rejected_outside_roots"})
        )
        return False

    logger.info("%s", json.dumps({**payload, "status": "deleting"}))
    safe_unlink_file(target, missing_ok=missing_ok)
    logger.info("%s", json.dumps({**payload, "status": "deleted"}))
    return True


class StagingCleanupError(RuntimeError):
    """A staging artifact could not be safely removed."""


def cleanup_staging_files(
    paths: Iterable[Path | None],
    *,
    label: str,
    allowed_roots: Iterable[Path] | None = None,
    protected_paths: Iterable[Path | None] = (),
) -> None:
    """Remove staging idempotently; rejected or remaining files are failures."""
    roots = tuple(staging_cleanup_roots() if allowed_roots is None else allowed_roots)
    protected = {path.resolve() for path in protected_paths if path is not None}
    for path in dict.fromkeys(paths):
        if path is None:
            continue
        if path.is_symlink() or path.resolve() in protected:
            raise StagingCleanupError(f"{label}: unsafe staging path {path}")
        safe_cleanup_staging_file(path, label=label, allowed_roots=roots)
        if path.exists() or path.is_symlink():
            raise StagingCleanupError(f"{label}: staging remains at {path}")


def cleanup_staging_after_commit(paths: Iterable[Path | None], *, label: str) -> None:
    # Publication has committed: log cleanup failures without revoking its result.
    staging_paths = tuple(paths)

    def cleanup_committed_staging() -> None:
        cleanup_staging_files(staging_paths, label=label)

    transaction.on_commit(cleanup_committed_staging, robust=True)


def cleanup_duplicate_import_staging(
    ctx: ImportContext,
    *,
    import_root: Path,
    sensitive_roots: Iterable[Path] | None = None,
) -> None:
    ctx.require_execution_ownership()
    cleanup_staging_files(
        (ctx.sensitive_path,),
        label=f"duplicate {ctx.file_type} sensitive copy",
        allowed_roots=sensitive_roots,
    )
    original = ctx.original_path
    if original is not None and original.parent.resolve() == import_root.resolve():
        ctx.require_execution_ownership()
        cleanup_staging_files(
            (original,),
            label=f"duplicate {ctx.file_type} import source",
            allowed_roots=(import_root,),
        )


__all__ = [
    "StagingCleanupError",
    "cleanup_staging_files",
    "cleanup_staging_after_commit",
    "cleanup_duplicate_import_staging",
    "is_safe_staging_path",
    "safe_cleanup_staging_file",
    "staging_cleanup_roots",
]
