from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from endoreg_db.utils.filesystem import paths as path_utils
from endoreg_db.utils.file_operations import safe_unlink_file

logger = logging.getLogger(__name__)


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def staging_cleanup_roots() -> tuple[Path, ...]:
    paths = path_utils.EndoregPathsModel.from_environment()
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
    roots = tuple(allowed_roots or staging_cleanup_roots())
    return any(_path_is_relative_to(target, root) for root in roots)


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
    roots = tuple(allowed_roots or staging_cleanup_roots())
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


__all__ = [
    "is_safe_staging_path",
    "safe_cleanup_staging_file",
    "staging_cleanup_roots",
]
