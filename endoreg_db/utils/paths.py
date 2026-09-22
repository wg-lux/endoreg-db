"""
Canonical runtime path topology for EndoReg-DB.

All application-owned paths derive from exactly one external configuration
value: ``LX_RUNTIME_ROOT``.

This module does not read legacy path aliases, rewrite environment variables,
or export generated module-level path constants. Callers should use
``get_runtime_paths()`` and typed model attributes.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from enum import StrEnum
from functools import lru_cache
from logging import getLogger
from pathlib import Path

from lx_dtypes.models.base.file.pydantic.FilesAndDirs import FilesAndDirsModel

from endoreg_db.config.env import (
    DEFAULT_DJANGO_SETTINGS_MODULE,
    DJANGO_SETTINGS_MODULE_ENV,
    RUNTIME_ROOT_ENV,
    get_runtime_root,
)

logger = getLogger(__name__)

IMPORT_DIR_NAME = "import"
EXPORT_DIR_NAME = "export"

IMPORT_VIDEO_DIR_NAME = "video_import"
REPORT_IMPORT_DIR_NAME = "report_import"
PREANONYMIZED_IMPORT_DIR_NAME = "preanonymized_import"
ANONYMIZED_VIDEO_IMPORT_DIR_NAME = "anonymized_video_import"
ANONYMIZED_REPORT_IMPORT_DIR_NAME = "anonymized_report_import"

VIDEO_EXPORT_DIR_NAME = "video_export"
REPORT_EXPORT_DIR_NAME = "report_export"

SENSITIVE_VIDEO_DIR_NAME = "sensitive_videos"
SENSITIVE_REPORT_DIR_NAME = "sensitive_reports"
ANONYM_VIDEO_DIR_NAME = "processed_videos_final"
ANONYM_REPORT_DIR_NAME = "processed_reports_final"

RAW_FRAME_DIR_NAME = "raw_frames"
FRAME_DIR_NAME = "frames"
WEIGHTS_DIR_NAME = "model_weights"
LOG_DIR_NAME = "logs"
QUARANTINE_DIR_NAME = "quarantine"
MIGRATION_STAGING_DIR_NAME = "migration_staging"
MANIFEST_DIR_NAME = "manifests"


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped


def _ensure_directory(path: Path) -> Path:
    from endoreg_db.utils.file_operations import ensure_directory

    return ensure_directory(path)


class EndoregPathsModel(FilesAndDirsModel):
    """Typed application path topology derived from one runtime root."""

    runtime_root: Path
    storage: Path
    terminology: Path

    import_dir: Path
    export_dir: Path

    import_video: Path
    import_report: Path
    import_preanonymized: Path
    import_anonymized_video: Path
    import_anonymized_report: Path

    video_export: Path
    report_export: Path

    documents: Path
    transcoding: Path
    sensitive_video: Path
    sensitive_report: Path
    anonym_video: Path
    anonym_report: Path
    raw_frame: Path
    frame: Path
    weights: Path

    weights_import: Path
    weights_export: Path
    import_frame: Path
    frame_export: Path

    logs: Path
    quarantine: Path
    migration_staging: Path
    manifest_dir: Path

    upload_api: Path
    upload_watcher: Path
    upload_preanonymized: Path

    watcher_video_drop: Path
    watcher_report_drop: Path
    watcher_preanonymized_drop: Path

    sap_import_drop: Path
    sap_import_processed: Path
    sap_import_failed: Path

    ingest_uploads: Path
    ingest_preanonymized: Path

    managed_anonymized_videos: Path
    managed_anonymized_reports: Path
    managed_sensitive_sidecars: Path

    quarantine_failed: Path
    staging_migration: Path
    test: Path

    lx_anonymizer_eval: Path
    streamable_videos_root: Path
    streamable_videos_raw_media: Path
    streamable_videos_processed_media: Path

    locks: Path

    @classmethod
    def from_root(cls, runtime_root: Path) -> "EndoregPathsModel":
        root = runtime_root.expanduser()

        if not root.is_absolute():
            raise ValueError("runtime_root must be absolute")

        root = root.resolve()

        storage = root / "storage"
        terminology = root / "terminology"

        import_dir = root / IMPORT_DIR_NAME
        export_dir = root / EXPORT_DIR_NAME
        quarantine_dir = root / QUARANTINE_DIR_NAME
        migration_staging_dir = root / MIGRATION_STAGING_DIR_NAME
        upload_jobs_dir = storage / "upload_jobs"
        test = storage / "test"
        lx_anonymizer_eval = storage / "lx_anonymizer_eval"
        streamable_videos_root = storage / "streamable_videos"

        locks = storage / "locks"

        path_values = {
            "runtime_root": root,
            "storage": storage,
            "terminology": terminology,
            "import_dir": import_dir,
            "export_dir": export_dir,
            "import_video": import_dir / IMPORT_VIDEO_DIR_NAME,
            "import_report": import_dir / REPORT_IMPORT_DIR_NAME,
            "import_preanonymized": import_dir / PREANONYMIZED_IMPORT_DIR_NAME,
            "import_anonymized_video": import_dir / ANONYMIZED_VIDEO_IMPORT_DIR_NAME,
            "import_anonymized_report": import_dir / ANONYMIZED_REPORT_IMPORT_DIR_NAME,
            "video_export": export_dir / VIDEO_EXPORT_DIR_NAME,
            "report_export": export_dir / REPORT_EXPORT_DIR_NAME,
            "documents": storage / "documents",
            "transcoding": storage / "temp",
            "sensitive_video": storage / SENSITIVE_VIDEO_DIR_NAME,
            "sensitive_report": storage / SENSITIVE_REPORT_DIR_NAME,
            "anonym_video": storage / ANONYM_VIDEO_DIR_NAME,
            "anonym_report": storage / ANONYM_REPORT_DIR_NAME,
            "raw_frame": storage / RAW_FRAME_DIR_NAME,
            "frame": storage / FRAME_DIR_NAME,
            "weights": storage / WEIGHTS_DIR_NAME,
            "weights_import": import_dir / WEIGHTS_DIR_NAME,
            "weights_export": export_dir / WEIGHTS_DIR_NAME,
            "import_frame": import_dir / FRAME_DIR_NAME,
            "frame_export": export_dir / FRAME_DIR_NAME,
            "logs": root / LOG_DIR_NAME,
            "quarantine": quarantine_dir,
            "migration_staging": migration_staging_dir,
            "manifest_dir": migration_staging_dir / MANIFEST_DIR_NAME,
            "upload_api": upload_jobs_dir / "api",
            "upload_watcher": upload_jobs_dir / "watcher",
            "upload_preanonymized": upload_jobs_dir / "preanonymized",
            "watcher_video_drop": import_dir / IMPORT_VIDEO_DIR_NAME,
            "watcher_report_drop": import_dir / REPORT_IMPORT_DIR_NAME,
            "watcher_preanonymized_drop": import_dir / PREANONYMIZED_IMPORT_DIR_NAME,
            "sap_import_drop": import_dir / "sap_import",
            "sap_import_processed": import_dir / "sap_import_processed",
            "sap_import_failed": import_dir / "sap_import_failed",
            "ingest_uploads": upload_jobs_dir,
            "ingest_preanonymized": import_dir / PREANONYMIZED_IMPORT_DIR_NAME,
            "managed_anonymized_videos": storage / ANONYM_VIDEO_DIR_NAME,
            "managed_anonymized_reports": storage / ANONYM_REPORT_DIR_NAME,
            "managed_sensitive_sidecars": storage / "sensitive_sidecars",
            "quarantine_failed": quarantine_dir / "failed",
            "staging_migration": migration_staging_dir,
            "test": test,
            "lx_anonymizer_eval": lx_anonymizer_eval,
            "streamable_videos_root": streamable_videos_root,
            "streamable_videos_raw_media": streamable_videos_root / "raw",
            "streamable_videos_processed_media": streamable_videos_root / "processed",
            "locks": locks,
        }

        return cls.model_validate(
            {
                "dir": root,
                "dirs": _dedupe_paths(path_values.values()),
                **path_values,
            }
        )

    @classmethod
    def from_environment(cls) -> "EndoregPathsModel":
        return cls.from_root(get_runtime_root())

    def ensure_directories(self) -> None:
        """Create the resolved runtime topology explicitly."""

        for path in self.dirs:
            _ensure_directory(path)


EndoregPathsModel.model_rebuild()


@lru_cache(maxsize=1)
def get_runtime_paths() -> EndoregPathsModel:
    """Return the process-wide immutable runtime topology."""

    return EndoregPathsModel.from_environment()


# Bind the reset hook to the cache itself so test overrides of the public
# resolver cannot break teardown or leave cached configuration behind.
clear_runtime_paths_cache: Callable[[], None] = get_runtime_paths.cache_clear


class StorageTier(StrEnum):
    UPLOAD_API = "upload_api"
    UPLOAD_WATCHER = "upload_watcher"
    UPLOAD_PREANONYMIZED = "upload_preanonymized"
    INGEST_UPLOADS = "ingest_uploads"
    INGEST_PREANONYMIZED = "ingest_preanonymized"
    MANAGED_ANONYMIZED_VIDEOS = "managed_anonymized_videos"
    MANAGED_ANONYMIZED_REPORTS = "managed_anonymized_reports"
    MANAGED_SENSITIVE_SIDECARS = "managed_sensitive_sidecars"
    WATCHER_VIDEO_DROP = "watcher_video_drop"
    WATCHER_REPORT_DROP = "watcher_report_drop"
    WATCHER_PREANONYMIZED_DROP = "watcher_preanonymized_drop"
    SAP_IMPORT_DROP = "sap_import_drop"
    SAP_IMPORT_PROCESSED = "sap_import_processed"
    SAP_IMPORT_FAILED = "sap_import_failed"
    MANIFEST = "manifest"
    MIGRATION_STAGING = "migration_staging"
    STAGING_MIGRATION = "staging_migration"
    QUARANTINE = "quarantine"
    QUARANTINE_FAILED = "quarantine_failed"


STORAGE_TIERS: frozenset[StorageTier] = frozenset(
    {
        StorageTier.UPLOAD_API,
        StorageTier.UPLOAD_WATCHER,
        StorageTier.UPLOAD_PREANONYMIZED,
        StorageTier.INGEST_UPLOADS,
        StorageTier.MANAGED_ANONYMIZED_VIDEOS,
        StorageTier.MANAGED_ANONYMIZED_REPORTS,
        StorageTier.MANAGED_SENSITIVE_SIDECARS,
    }
)

STORAGE_TIER_FIELDS: dict[StorageTier, str] = {
    tier: "manifest_dir" if tier == StorageTier.MANIFEST else tier.value
    for tier in StorageTier
}


def _ensure_within(
    path: str | Path,
    *,
    root: Path,
    label: str,
) -> Path:
    resolved_path = Path(path).expanduser().resolve()
    resolved_root = root.resolve()

    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Path {resolved_path} is outside {label} {resolved_root}")

    return resolved_path


def ensure_within_runtime_root(path: str | Path) -> Path:
    return _ensure_within(
        path,
        root=get_runtime_paths().runtime_root,
        label="runtime root",
    )


def ensure_within_storage_root(path: str | Path) -> Path:
    return _ensure_within(
        path,
        root=get_runtime_paths().storage,
        label="storage root",
    )


def protected_media_root() -> Path:
    """Return the canonical protected-media root."""

    return get_runtime_paths().storage


def normalize_protected_media_relative_path(relative_path: str | Path) -> str:
    candidate = Path(str(relative_path or "").strip())

    if str(candidate) == "":
        raise ValueError("Protected media path must not be empty")
    if candidate.is_absolute():
        raise ValueError("Protected media path must be relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"Protected media path is not safe: {relative_path}")

    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        raise ValueError("Protected media path must not be empty")

    return normalized


def ensure_within_protected_media_root(path: str | Path) -> Path:
    """Semantic alias for the storage security boundary."""

    return ensure_within_storage_root(path)


def to_protected_media_relative(path: str | Path) -> str:
    resolved_path = ensure_within_storage_root(path)
    return resolved_path.relative_to(get_runtime_paths().storage).as_posix()


def resolve_protected_media_path(relative_path: str | Path) -> Path:
    normalized = normalize_protected_media_relative_path(relative_path)
    return ensure_within_storage_root(get_runtime_paths().storage / normalized)


def resolve_existing_protected_media_path(path_value: str | Path) -> Path | None:
    candidate = Path(path_value)

    try:
        if candidate.is_absolute():
            return ensure_within_storage_root(candidate.resolve(strict=True))

        return resolve_protected_media_path(candidate).resolve(strict=True)
    except (FileNotFoundError, ValueError):
        return None


def to_storage_relative(path: str | Path) -> str:
    """Return a Django FileField name relative to the canonical storage root."""

    resolved = ensure_within_storage_root(path)
    return resolved.relative_to(get_runtime_paths().storage).as_posix()


def to_runtime_relative(path: str | Path) -> str:
    """Return a path relative to the canonical application runtime root."""

    resolved = ensure_within_runtime_root(path)
    return resolved.relative_to(get_runtime_paths().runtime_root).as_posix()


def resolve_runtime_path(relative_path: str | Path) -> Path:
    """Resolve a safe relative path beneath the canonical runtime root."""

    raw_value = str(relative_path).strip()
    if not raw_value:
        raise ValueError("Runtime-relative path must not be empty")
    candidate = Path(raw_value)
    if candidate.is_absolute():
        raise ValueError("Runtime-relative path must be relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"Runtime-relative path is not safe: {relative_path}")

    return ensure_within_runtime_root(get_runtime_paths().runtime_root / candidate)


def _sanitize_path_token(value: str) -> str:
    allowed: list[str] = []
    for char in value.strip():
        if char.isalnum():
            allowed.append(char.lower())
        elif char in {"-", "_"}:
            allowed.append(char)
        else:
            allowed.append("_")

    collapsed = "".join(allowed).strip("_")
    return collapsed or "artifact"


def _coerce_storage_tier(tier: str | StorageTier) -> StorageTier:
    raw_value = getattr(tier, "value", tier)
    try:
        return StorageTier(str(raw_value))
    except ValueError as exc:
        raise KeyError(f"Unknown storage tier: {tier}") from exc


def get_storage_tier_root(tier: str | StorageTier) -> Path:
    tier_key = _coerce_storage_tier(tier)
    return getattr(get_runtime_paths(), STORAGE_TIER_FIELDS[tier_key])


def resolve_storage_tier_path(
    tier: str | StorageTier,
    *parts: str | Path,
) -> Path:
    tier_key = _coerce_storage_tier(tier)
    root = get_storage_tier_root(tier_key)
    candidate = root.joinpath(*(str(part) for part in parts)).resolve()

    if tier_key in STORAGE_TIERS:
        return ensure_within_storage_root(candidate)

    return ensure_within_runtime_root(candidate)


def build_upload_job_relative_path(
    *,
    tier: str | StorageTier,
    filename: str,
    key: str,
) -> str:
    tier_key = _coerce_storage_tier(tier)
    if tier_key not in STORAGE_TIERS:
        raise ValueError(
            f"Upload job tier must be storage-backed, got {tier_key.value!r}"
        )

    sanitized_name = Path(filename).name or "upload.bin"
    candidate = resolve_storage_tier_path(
        tier_key,
        key[:2] or "00",
        key,
        sanitized_name,
    )
    return candidate.relative_to(get_runtime_paths().storage).as_posix()


def build_manifest_path(*, command_name: str, stem: str) -> Path:
    command_token = _sanitize_path_token(command_name)
    stem_token = _sanitize_path_token(stem)

    return resolve_storage_tier_path(
        StorageTier.MANIFEST,
        command_token,
        f"{stem_token}.json",
    )


def resolve_protected_runtime_path(
    raw_path: str | Path | None,
    *,
    fallback: Path,
) -> Path:
    """Resolve an optional runtime path under the canonical runtime root.

    ``fallback`` must itself be inside the runtime root. Explicit values may be
    absolute or runtime-relative, but they may never escape the runtime root.
    """

    fallback_path = ensure_within_runtime_root(fallback)

    if raw_path in (None, ""):
        return fallback_path

    candidate = Path(str(raw_path)).expanduser()
    if not candidate.is_absolute():
        candidate = get_runtime_paths().runtime_root / candidate

    return ensure_within_runtime_root(candidate)


def _is_production_runtime() -> bool:
    settings_module = os.environ.get(
        DJANGO_SETTINGS_MODULE_ENV,
        DEFAULT_DJANGO_SETTINGS_MODULE,
    ).strip()

    return (
        os.environ.get("DJANGO_ENV", "").strip().lower() == "production"
        or settings_module.endswith(".prod")
        or settings_module.endswith(".settings_prod")
    )


def validate_runtime_storage_contract() -> None:
    """Validate the one-root runtime topology without repairing it implicitly."""

    paths = get_runtime_paths()

    if _is_production_runtime() and not os.environ.get(RUNTIME_ROOT_ENV, "").strip():
        raise RuntimeError(
            f"{RUNTIME_ROOT_ENV} must be explicitly configured in production."
        )

    storage_paths = {
        "storage": paths.storage,
        "upload_api": paths.upload_api,
        "upload_watcher": paths.upload_watcher,
        "upload_preanonymized": paths.upload_preanonymized,
        "managed_anonymized_videos": paths.managed_anonymized_videos,
        "managed_anonymized_reports": paths.managed_anonymized_reports,
        "managed_sensitive_sidecars": paths.managed_sensitive_sidecars,
        "transcoding": paths.transcoding,
        "streamable_videos_root": paths.streamable_videos_root,
        "streamable_videos_raw_media": paths.streamable_videos_raw_media,
        "streamable_videos_processed_media": paths.streamable_videos_processed_media,
        "lx_anonymizer_eval": paths.lx_anonymizer_eval,
    }

    for path in paths.dirs:
        try:
            ensure_within_runtime_root(path)
        except ValueError as exc:
            raise RuntimeError(
                f"Runtime path contract invalid for {path}: {exc}"
            ) from exc
        if not path.is_dir():
            raise RuntimeError(
                f"Runtime path does not exist or is not a directory: {path}"
            )
        if not os.access(path, os.W_OK):
            raise RuntimeError(f"Runtime path is not writable: {path}")

    for label, path in storage_paths.items():
        try:
            ensure_within_storage_root(path)
        except ValueError as exc:
            raise RuntimeError(
                f"Storage path contract invalid for {label}: {exc}"
            ) from exc


__all__ = [
    "EndoregPathsModel",
    "StorageTier",
    "STORAGE_TIERS",
    "STORAGE_TIER_FIELDS",
    "get_runtime_paths",
    "clear_runtime_paths_cache",
    "ensure_within_runtime_root",
    "ensure_within_storage_root",
    "protected_media_root",
    "normalize_protected_media_relative_path",
    "ensure_within_protected_media_root",
    "to_protected_media_relative",
    "resolve_protected_media_path",
    "resolve_existing_protected_media_path",
    "to_storage_relative",
    "to_runtime_relative",
    "resolve_runtime_path",
    "get_storage_tier_root",
    "resolve_storage_tier_path",
    "build_upload_job_relative_path",
    "build_manifest_path",
    "resolve_protected_runtime_path",
    "validate_runtime_storage_contract",
]
