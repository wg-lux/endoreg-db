"""
Centralized path management for the application.

The module exposes the historical path constants plus a dict-like ``data_paths``
mapping, but uses a Pydantic model as the single source of truth so path
resolution and directory bootstrap stay consistent.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from enum import StrEnum
from logging import getLogger
from pathlib import Path
from typing import ClassVar

from lx_dtypes.models.base.file.pydantic.FilesAndDirs import FilesAndDirsModel

from endoreg_db.config.env import (
    BASE_DIR,
    DATA_DIR_ENV,
    DJANGO_SETTINGS_MODULE,
    PROTECTED_MEDIA_ROOT_ENV,
    PROTECTED_ROOT_ENV,
    STORAGE_DIR_ENV,
    TEST_DATA_ROOT,
    TEST_PROTECTED_ROOT,
    env_path,
)

# Directory topology is split between a protected storage root and a data root.
# Watcher intake lives under data/import; managed media lives under storage.
# It is possible to set up one directory inside the other.

logger = getLogger(__name__)

PREFIX_RAW = "raw_"

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

RAW_FRAME_DIR_NAME = f"{PREFIX_RAW}frames"
FRAME_DIR_NAME = "frames"
WEIGHTS_DIR_NAME = "model_weights"
LOG_DIR_NAME = "logs"
QUARANTINE_DIR_NAME = "quarantine"
MIGRATION_STAGING_DIR_NAME = "migration_staging"
MANIFEST_DIR_NAME = "manifests"


class EndoregPathsModel(FilesAndDirsModel):
    """Pydantic-backed container for all application directories."""

    protected_root: Path
    storage: Path
    data: Path
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

    legacy_key_map: ClassVar[dict[str, str]] = {}

    @classmethod
    def from_environment(cls) -> "EndoregPathsModel":
        protected_root = _resolve_protected_root()
        data_dir = _resolve_data_root()
        storage_dir = _resolve_protected_subdir(
            env_key=STORAGE_DIR_ENV,
            default_path=protected_root / "storage",
            protected_root=protected_root,
        )
        import_dir = data_dir / IMPORT_DIR_NAME
        migration_staging_dir = data_dir / MIGRATION_STAGING_DIR_NAME
        export_dir = data_dir / EXPORT_DIR_NAME
        quarantine_dir = data_dir / QUARANTINE_DIR_NAME
        upload_jobs_dir = storage_dir / "upload_jobs"

        path_values = {
            "protected_root": protected_root,
            "storage": storage_dir,
            "data": data_dir,
            "import_dir": import_dir,
            "export_dir": export_dir,
            "import_video": import_dir / IMPORT_VIDEO_DIR_NAME,
            "import_report": import_dir / REPORT_IMPORT_DIR_NAME,
            "import_preanonymized": import_dir / PREANONYMIZED_IMPORT_DIR_NAME,
            "import_anonymized_video": import_dir / ANONYMIZED_VIDEO_IMPORT_DIR_NAME,
            "import_anonymized_report": import_dir / ANONYMIZED_REPORT_IMPORT_DIR_NAME,
            "video_export": export_dir / VIDEO_EXPORT_DIR_NAME,
            "report_export": export_dir / REPORT_EXPORT_DIR_NAME,
            "documents": storage_dir / "documents",
            "transcoding": storage_dir / "temp",
            "sensitive_video": storage_dir / SENSITIVE_VIDEO_DIR_NAME,
            "sensitive_report": storage_dir / SENSITIVE_REPORT_DIR_NAME,
            "anonym_video": storage_dir / ANONYM_VIDEO_DIR_NAME,
            "anonym_report": storage_dir / ANONYM_REPORT_DIR_NAME,
            "raw_frame": storage_dir / RAW_FRAME_DIR_NAME,
            "frame": storage_dir / FRAME_DIR_NAME,
            "weights": storage_dir / WEIGHTS_DIR_NAME,
            "weights_import": import_dir / WEIGHTS_DIR_NAME,
            "weights_export": export_dir / WEIGHTS_DIR_NAME,
            "import_frame": import_dir / FRAME_DIR_NAME,
            "frame_export": export_dir / FRAME_DIR_NAME,
            "logs": data_dir / LOG_DIR_NAME,
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
            "managed_anonymized_videos": storage_dir / ANONYM_VIDEO_DIR_NAME,
            "managed_anonymized_reports": storage_dir / ANONYM_REPORT_DIR_NAME,
            "managed_sensitive_sidecars": storage_dir / "sensitive_sidecars",
            "quarantine_failed": quarantine_dir / "failed",
            "staging_migration": migration_staging_dir,
            "test": storage_dir / "test",
        }

        instance = cls.model_validate(
            {
                "dir": storage_dir,
                "dirs": _dedupe_paths(path_values.values()),
                **path_values,
            }
        )
        instance.ensure_directories()
        return instance

    def ensure_directories(self) -> None:
        for path in self.dirs:
            _ensure_directory(path)

    def as_dict(self) -> dict[str, Path]:
        return {key: self[key] for key in self.legacy_key_map}

    def __getitem__(self, key: str) -> Path:
        try:
            field_name = self.legacy_key_map[key]
        except KeyError as exc:
            raise KeyError(f"Unknown data path key: {key}") from exc
        return getattr(self, field_name)

    def __len__(self) -> int:
        return len(self.legacy_key_map)

    def keys(self) -> Iterable[str]:
        return self.legacy_key_map.keys()

    def items(self) -> Iterable[tuple[str, Path]]:
        return ((key, self[key]) for key in self.legacy_key_map)

    def values(self) -> Iterable[Path]:
        return (self[key] for key in self.legacy_key_map)


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


PROTECTED_STORAGE_TIERS: frozenset[StorageTier] = frozenset(
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


def _resolve_env_path(raw_value: str) -> Path:
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (BASE_DIR / candidate).resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    return path.is_relative_to(root)


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped


def _ensure_directory(path: Path) -> Path:
    from endoreg_db.utils.file_operations import ensure_directory

    return ensure_directory(path)


def _legacy_test_roots(*roots: Path) -> list[Path]:
    if not TEST_PATH_COMPAT_ENABLED:
        return []
    return [root.resolve() for root in roots]


def _ensure_within_roots(
    path: str | Path,
    *,
    roots: Iterable[Path],
    label: str,
) -> Path:
    resolved_path = Path(path).resolve()
    root_list = _dedupe_paths(root.resolve() for root in roots)
    for root in root_list:
        if resolved_path.is_relative_to(root):
            return resolved_path
    raise ValueError(f"Path {resolved_path} is outside {label} {root_list[0]}")


def _relative_to_any(path: Path, roots: Iterable[Path]) -> str | None:
    for root in _dedupe_paths(root.resolve() for root in roots):
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return None


def _test_path_compat_enabled() -> bool:
    """Allow legacy test roots only for explicit test settings inside data/tests."""
    if os.environ.get("DJANGO_ENV", "").strip().lower() == "production":
        return False

    settings_module = os.environ.get("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)
    is_test_settings = settings_module in {
        "endoreg_db.config.settings.test",
        "tests.settings_test",
    } or settings_module.endswith(".settings.test")
    if not is_test_settings:
        return False

    test_root = (BASE_DIR / "data" / "tests").resolve()
    protected_root = _resolve_env_path(
        os.environ.get(PROTECTED_ROOT_ENV, str(TEST_PROTECTED_ROOT))
    )
    data_root = _resolve_env_path(os.environ.get(DATA_DIR_ENV, str(TEST_DATA_ROOT)))
    return _is_relative_to(protected_root, test_root) and _is_relative_to(
        data_root,
        test_root,
    )


TEST_PATH_COMPAT_ENABLED = _test_path_compat_enabled()


def _resolve_protected_root() -> Path:
    return env_path(PROTECTED_ROOT_ENV, "data").resolve()


def _resolve_data_root() -> Path:
    return env_path(DATA_DIR_ENV, "data").resolve()


def _resolve_protected_subdir(
    *,
    env_key: str,
    default_path: Path,
    protected_root: Path,
) -> Path:
    raw_value = os.environ.get(env_key, "").strip()
    if not raw_value:
        return default_path

    candidate = _resolve_env_path(raw_value)
    try:
        candidate.relative_to(protected_root)
    except ValueError as exc:
        raise RuntimeError(
            f"{env_key} must resolve inside {PROTECTED_ROOT_ENV}: "
            f"{candidate} is outside {protected_root}"
        ) from exc
    return candidate


def ensure_within_protected_root(path: str | Path) -> Path:
    current_protected_root = (
        EndoregPathsModel.from_environment().protected_root.resolve()
    )
    return _ensure_within_roots(
        path,
        roots=[
            current_protected_root,
            *_legacy_test_roots(
                TEST_PROTECTED_ROOT,
                BASE_DIR / "data" / "tests" / "storage",
            ),
        ],
        label="protected data root",
    )


def ensure_within_data_root(path: str | Path) -> Path:
    current_data_root = EndoregPathsModel.from_environment().data.resolve()
    return _ensure_within_roots(
        path,
        roots=[
            current_data_root,
            *_legacy_test_roots(
                TEST_DATA_ROOT,
                BASE_DIR / "data" / "tests" / "storage",
            ),
        ],
        label="data root",
    )


def _resolve_protected_media_root() -> Path:
    raw_value = os.environ.get(PROTECTED_MEDIA_ROOT_ENV, "").strip()
    if not raw_value:
        return EndoregPathsModel.from_environment().storage.resolve()
    return ensure_within_protected_root(_resolve_env_path(raw_value))


def protected_media_root() -> Path:
    return _resolve_protected_media_root()


def normalize_protected_media_relative_path(relative_path: str | Path) -> str:
    candidate = Path(str(relative_path or "").strip())
    if str(candidate) == "":
        raise ValueError("Protected media path must not be empty")
    if candidate.is_absolute():
        raise ValueError("Protected media path must be relative")
    if any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"Protected media path is not safe: {relative_path}")
    normalized = Path(*candidate.parts).as_posix()
    if normalized in {"", "."}:
        raise ValueError("Protected media path must not be empty")
    return normalized


def ensure_within_protected_media_root(path: str | Path) -> Path:
    return _ensure_within_roots(
        path,
        roots=[_resolve_protected_media_root()],
        label="protected media root",
    )


def to_protected_media_relative(path: str | Path) -> str:
    resolved_path = ensure_within_protected_media_root(path)
    protected_media_root = _resolve_protected_media_root()
    return resolved_path.relative_to(protected_media_root).as_posix()


def resolve_protected_media_path(relative_path: str | Path) -> Path:
    normalized = normalize_protected_media_relative_path(relative_path)
    return ensure_within_protected_media_root(
        _resolve_protected_media_root() / normalized
    )


def resolve_existing_protected_media_path(path_value: str | Path) -> Path | None:
    candidate = Path(path_value)

    if candidate.is_absolute():
        try:
            resolved_absolute = candidate.resolve(strict=True)
            return ensure_within_protected_media_root(resolved_absolute)
        except (FileNotFoundError, ValueError):
            return None

    try:
        return resolve_protected_media_path(candidate).resolve(strict=True)
    except (FileNotFoundError, ValueError):
        return None


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


LEGACY_KEY_EXCLUDE_FIELDS = {"protected_root"}
LEGACY_KEY_OVERRIDES = {"import_dir": "import", "export_dir": "export"}
PATH_EXPORT_EXCLUDE_FIELDS = {"test"}
PATH_EXPORT_OVERRIDES = {
    "protected_root": "PROTECTED_DATA_ROOT",
    "documents": "DOCUMENT_DIR",
    "import_frame": "FRAME_IMPORT_DIR",
    "logs": "LOG_DIR",
}


def _path_model_field_names() -> tuple[str, ...]:
    return tuple(
        field_name
        for field_name in EndoregPathsModel.__annotations__
        if field_name != "legacy_key_map"
    )


def _build_legacy_key_map(field_names: Iterable[str]) -> dict[str, str]:
    return {
        LEGACY_KEY_OVERRIDES.get(field_name, field_name): field_name
        for field_name in field_names
        if field_name not in LEGACY_KEY_EXCLUDE_FIELDS
    }


def _default_export_name(field_name: str) -> str:
    base_name = field_name.removesuffix("_dir")
    return f"{base_name.upper()}_DIR"


def _build_path_exports(field_names: Iterable[str]) -> dict[str, str]:
    return {
        PATH_EXPORT_OVERRIDES.get(
            field_name, _default_export_name(field_name)
        ): field_name
        for field_name in field_names
        if field_name not in PATH_EXPORT_EXCLUDE_FIELDS
    }


EndoregPathsModel.model_rebuild()
PATH_MODEL_FIELDS = _path_model_field_names()
LEGACY_KEY_MAP = _build_legacy_key_map(PATH_MODEL_FIELDS)
PATH_EXPORTS = _build_path_exports(PATH_MODEL_FIELDS)
EndoregPathsModel.legacy_key_map = LEGACY_KEY_MAP

# Static declarations for path constants assigned by rebind_path_exports().
PROTECTED_DATA_ROOT: Path
STORAGE_DIR: Path
DATA_DIR: Path
IMPORT_DIR: Path
EXPORT_DIR: Path
IMPORT_VIDEO_DIR: Path
IMPORT_REPORT_DIR: Path
IMPORT_PREANONYMIZED_DIR: Path
IMPORT_ANONYMIZED_VIDEO_DIR: Path
IMPORT_ANONYMIZED_REPORT_DIR: Path
VIDEO_EXPORT_DIR: Path
REPORT_EXPORT_DIR: Path
DOCUMENT_DIR: Path
TRANSCODING_DIR: Path
SENSITIVE_VIDEO_DIR: Path
SENSITIVE_REPORT_DIR: Path
ANONYM_VIDEO_DIR: Path
ANONYM_REPORT_DIR: Path
RAW_FRAME_DIR: Path
FRAME_DIR: Path
WEIGHTS_DIR: Path
WEIGHTS_IMPORT_DIR: Path
WEIGHTS_EXPORT_DIR: Path
FRAME_IMPORT_DIR: Path
FRAME_EXPORT_DIR: Path
LOG_DIR: Path
QUARANTINE_DIR: Path
MIGRATION_STAGING_DIR: Path
MANIFEST_DIR: Path
UPLOAD_API_DIR: Path
UPLOAD_WATCHER_DIR: Path
UPLOAD_PREANONYMIZED_DIR: Path
WATCHER_VIDEO_DROP_DIR: Path
WATCHER_REPORT_DROP_DIR: Path
WATCHER_PREANONYMIZED_DROP_DIR: Path
SAP_IMPORT_DROP_DIR: Path
SAP_IMPORT_PROCESSED_DIR: Path
SAP_IMPORT_FAILED_DIR: Path
INGEST_UPLOADS_DIR: Path
INGEST_PREANONYMIZED_DIR: Path
MANAGED_ANONYMIZED_VIDEOS_DIR: Path
MANAGED_ANONYMIZED_REPORTS_DIR: Path
MANAGED_SENSITIVE_SIDECARS_DIR: Path
QUARANTINE_FAILED_DIR: Path
STAGING_MIGRATION_DIR: Path


def rebind_path_exports(model: EndoregPathsModel) -> None:
    for export_name, field_name in PATH_EXPORTS.items():
        globals()[export_name] = getattr(model, field_name)


data_paths_model = EndoregPathsModel.from_environment()
data_paths = data_paths_model
rebind_path_exports(data_paths_model)

logger.debug("Protected data root: %s", data_paths_model.protected_root.resolve())
logger.debug("Data directory: %s", data_paths_model.data.resolve())
logger.debug("Encrypted storage directory: %s", data_paths_model.storage.resolve())
logger.debug("Export directory: %s", data_paths_model.export_dir.resolve())


def to_storage_relative(path: str | Path) -> str:
    """
    Return a path string relative to STORAGE_DIR, suitable for Django FileField.name.

    Local DATA_DIR paths are returned relative to DATA_DIR. If ``path`` is
    outside STORAGE_DIR and DATA_DIR, protected-root paths are returned
    unchanged after validation.
    """
    original_path = str(path)
    resolved_path = Path(path).resolve()
    current_paths = EndoregPathsModel.from_environment()
    legacy_storage = BASE_DIR / "data" / "tests" / "storage"
    relative_path = _relative_to_any(
        resolved_path,
        [current_paths.storage, *_legacy_test_roots(legacy_storage)],
    )
    if relative_path is not None:
        return relative_path

    relative_path = _relative_to_any(
        resolved_path,
        [current_paths.data, *_legacy_test_roots(TEST_DATA_ROOT, legacy_storage)],
    )
    if relative_path is not None:
        return relative_path

    ensure_within_protected_root(resolved_path)
    return original_path


def to_protected_relative(path: str | Path) -> str:
    return (
        ensure_within_protected_root(path)
        .relative_to(EndoregPathsModel.from_environment().protected_root.resolve())
        .as_posix()
    )


def _coerce_storage_tier(tier: str | StorageTier) -> StorageTier:
    raw_value = getattr(tier, "value", tier)
    try:
        return StorageTier(str(raw_value))
    except ValueError as exc:
        raise KeyError(f"Unknown storage tier: {tier}") from exc


def get_storage_tier_root(tier: str | StorageTier) -> Path:
    tier_key = _coerce_storage_tier(tier)
    current_paths = EndoregPathsModel.from_environment()
    return getattr(current_paths, STORAGE_TIER_FIELDS[tier_key])


def validate_runtime_storage_contract() -> None:
    protected_root_env = os.environ.get(PROTECTED_ROOT_ENV, "").strip()
    django_env = os.environ.get("DJANGO_ENV", "").strip().lower()
    is_production = django_env == "production"
    current_paths = EndoregPathsModel.from_environment()

    if not protected_root_env:
        raise RuntimeError(
            f"{PROTECTED_ROOT_ENV} must be set for the protected runtime contract."
        )

    protected_paths_to_validate = {
        "protected_root": current_paths.protected_root,
        "storage": current_paths.storage,
        "upload_api": current_paths.upload_api,
        "upload_watcher": current_paths.upload_watcher,
        "upload_preanonymized": current_paths.upload_preanonymized,
    }
    public_paths_to_validate = {
        "data_root": current_paths.data,
        "import": current_paths.import_dir,
        "export": current_paths.export_dir,
        "logs": current_paths.logs,
        "quarantine": current_paths.quarantine,
        "migration_staging": current_paths.migration_staging,
        "manifest": current_paths.manifest_dir,
        "watcher_video_drop": current_paths.watcher_video_drop,
        "watcher_report_drop": current_paths.watcher_report_drop,
        "watcher_preanonymized_drop": current_paths.watcher_preanonymized_drop,
        "sap_import_drop": current_paths.sap_import_drop,
        "sap_import_processed": current_paths.sap_import_processed,
        "sap_import_failed": current_paths.sap_import_failed,
    }
    for label, path in protected_paths_to_validate.items():
        try:
            ensure_within_protected_root(path)
        except ValueError as exc:
            raise RuntimeError(
                f"Runtime storage contract invalid for {label}: {exc}"
            ) from exc
    for label, path in public_paths_to_validate.items():
        try:
            ensure_within_data_root(path)
        except ValueError as exc:
            raise RuntimeError(
                f"Runtime data contract invalid for {label}: {exc}"
            ) from exc

    for label, path in {
        **protected_paths_to_validate,
        **public_paths_to_validate,
    }.items():
        if not path.exists():
            if TEST_PATH_COMPAT_ENABLED:
                _ensure_directory(path)
                continue
            raise RuntimeError(
                f"Runtime storage path does not exist for {label}: {path}"
            )
        if not os.access(path, os.W_OK):
            raise RuntimeError(
                f"Runtime storage path is not writable for {label}: {path}"
            )
        if is_production and BASE_DIR.resolve() in path.resolve().parents:
            raise RuntimeError(
                f"Production storage path for {label} must not resolve inside repo root: {path}"
            )


def resolve_storage_tier_path(tier: str | StorageTier, *parts: str | Path) -> Path:
    tier_key = _coerce_storage_tier(tier)
    root = get_storage_tier_root(tier_key)
    candidate = root.joinpath(*[str(part) for part in parts]).resolve()
    if tier_key in PROTECTED_STORAGE_TIERS:
        return ensure_within_protected_root(candidate)
    return ensure_within_data_root(candidate)


def build_upload_job_relative_path(
    *,
    tier: str | StorageTier,
    filename: str,
    key: str,
) -> str:
    sanitized_name = Path(filename).name or "upload.bin"
    current_storage_root = EndoregPathsModel.from_environment().storage.resolve()
    relative_path = resolve_storage_tier_path(
        tier,
        key[:2] or "00",
        key,
        sanitized_name,
    ).relative_to(current_storage_root)
    return relative_path.as_posix()


def build_manifest_path(*, command_name: str, stem: str) -> Path:
    command_token = _sanitize_path_token(command_name)
    stem_token = _sanitize_path_token(stem)
    return resolve_storage_tier_path(
        "manifest",
        command_token,
        f"{stem_token}.json",
    )


def resolve_protected_runtime_path(
    raw_path: str | Path | None,
    *,
    fallback: Path,
) -> Path:
    if raw_path in (None, ""):
        return Path(fallback).expanduser().resolve()
    return ensure_within_protected_root(_resolve_env_path(str(raw_path)))
