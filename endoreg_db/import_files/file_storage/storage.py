import logging
from pathlib import Path
from uuid import uuid4

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.schemas.report_import import ReportSourceSnapshot
from endoreg_db.services.video_files._imports import atomic_copy_with_fallback
from endoreg_db.utils.file_operations import ensure_directory
from endoreg_db.utils.filesystem.file_operations import atomic_report_source_snapshot

logger = logging.getLogger(__name__)


def ensure_dir(path: Path) -> None:
    ensure_directory(path)


def create_sensitive_copy(src: Path, sensitive_root: Path, ctx: ImportContext) -> Path:
    """
    Create a sensitive copy of `src` in `sensitive_root`.

    Returns:
        Path to the sensitive copy.
    """
    ensure_dir(sensitive_root)
    hash_prefix = str(getattr(ctx, "file_hash", None) or "unhashed")[:16]
    staging_dir = ensure_directory(sensitive_root / f"{hash_prefix}-{uuid4().hex}")
    dest = staging_dir / src.name
    logger.info("Creating sensitive copy: %s -> %s", src, dest)
    atomic_copy_with_fallback(src, dest)
    return dest


def create_sensitive_report_snapshot(
    src: Path,
    sensitive_root: Path,
) -> ReportSourceSnapshot:
    """Create one immutable, content-addressable report import snapshot."""
    ensure_dir(sensitive_root)
    staging_dir = ensure_directory(sensitive_root / f"report-attempt-{uuid4().hex}")
    destination = staging_dir / src.name
    logger.info("Creating stable sensitive report snapshot: %s -> %s", src, destination)
    return atomic_report_source_snapshot(
        source=src,
        destination=destination,
    )
