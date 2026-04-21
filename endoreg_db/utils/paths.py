"""
Centralized path management for the application.

The module exposes the historical path constants plus a dict-like ``data_paths``
mapping, but uses a Pydantic model as the single source of truth so path
resolution and directory bootstrap stay consistent.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
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

"""
<protected_root>/                  # usually BASE_DIR/data
├── storage/                       # protected storage root
│   ├── upload_jobs/
│   │   ├── api/
│   │   ├── watcher/
│   │   └── preanonymized/
│   ├── documents/
│   ├── temp/
│   ├── sensitive_videos/
│   ├── sensitive_reports/
│   ├── processed_videos_final/
│   ├── processed_reports_final/
│   ├── raw_frames/
│   ├── frames/
│   ├── model_weights/
│   ├── sensitive_sidecars/
│   └── test/
│
└── ... possibly same as data root if configured that way


<data_root>/                       # usually BASE_DIR/data
├── import/
│   ├── video_import/
│   ├── report_import/
│   ├── preanonymized_import/
│   ├── anonymized_video_import/
│   ├── anonymized_report_import/
│   ├── model_weights/
│   └── frames/
├── export/
│   ├── video_export/
│   ├── report_export/
│   ├── model_weights/
│   └── frames/
├── logs/
├── quarantine/
│   └── failed/
├── migration_staging/
│   └── manifests/
├── sap_import/
├── sap_import_processed/
└── sap_import_failed/
"""

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


def _resolve_env_path(raw_value: str) -> Path:
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (BASE_DIR / candidate).resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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
    resolved_path = Path(path).resolve()
    current_protected_root = (
        EndoregPathsModel.from_environment().protected_root.resolve()
    )
    protected_roots = [current_protected_root]
    if TEST_PATH_COMPAT_ENABLED:
        legacy_test_roots = [
            TEST_PROTECTED_ROOT.resolve(),
            (BASE_DIR / "data" / "tests" / "storage").resolve(),
        ]
        for legacy_root in legacy_test_roots:
            if legacy_root not in protected_roots:
                protected_roots.append(legacy_root)

    for protected_root in protected_roots:
        try:
            resolved_path.relative_to(protected_root)
            return resolved_path
        except ValueError:
            continue

    protected_root = current_protected_root
    raise ValueError(
        f"Path {resolved_path} is outside protected data root {protected_root}"
    )


def ensure_within_data_root(path: str | Path) -> Path:
    resolved_path = Path(path).resolve()
    current_data_root = EndoregPathsModel.from_environment().data.resolve()
    data_roots = [current_data_root]
    if TEST_PATH_COMPAT_ENABLED:
        legacy_test_roots = [
            TEST_DATA_ROOT.resolve(),
            (BASE_DIR / "data" / "tests" / "storage").resolve(),
        ]
        for legacy_root in legacy_test_roots:
            if legacy_root not in data_roots:
                data_roots.append(legacy_root)

    for data_root in data_roots:
        try:
            resolved_path.relative_to(data_root)
            return resolved_path
        except ValueError:
            continue

    raise ValueError(f"Path {resolved_path} is outside data root {current_data_root}")


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
    resolved_path = Path(path).resolve()
    protected_media_root = _resolve_protected_media_root()
    try:
        resolved_path.relative_to(protected_media_root)
    except ValueError as exc:
        raise ValueError(
            f"Path {resolved_path} is outside protected media root {protected_media_root}"
        ) from exc
    return resolved_path


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

    # If any directory names change, please ensure continued support by changing the values  in key: value.
    legacy_key_map: ClassVar[dict[str, str]] = {
        "data": "data",
        "storage": "storage",
        "import": "import_dir",
        "import_video": "import_video",
        "import_report": "import_report",
        "import_preanonymized": "import_preanonymized",
        "import_anonymized_video": "import_anonymized_video",
        "import_anonymized_report": "import_anonymized_report",
        "sensitive_video": "sensitive_video",
        "sensitive_report": "sensitive_report",
        "anonym_video": "anonym_video",
        "anonym_report": "anonym_report",
        "import_frame": "import_frame",
        "raw_frame": "raw_frame",
        "weights": "weights",
        "weights_import": "weights_import",
        "export": "export_dir",
        "report_export": "report_export",
        "video_export": "video_export",
        "frame_export": "frame_export",
        "weights_export": "weights_export",
        "transcoding": "transcoding",
        "frame": "frame",
        "documents": "documents",
        "logs": "logs",
        "quarantine": "quarantine",
        "migration_staging": "migration_staging",
        "manifest_dir": "manifest_dir",
        "upload_api": "upload_api",
        "upload_watcher": "upload_watcher",
        "upload_preanonymized": "upload_preanonymized",
        "watcher_video_drop": "watcher_video_drop",
        "watcher_report_drop": "watcher_report_drop",
        "watcher_preanonymized_drop": "watcher_preanonymized_drop",
        "sap_import_drop": "sap_import_drop",
        "sap_import_processed": "sap_import_processed",
        "sap_import_failed": "sap_import_failed",
        "ingest_uploads": "ingest_uploads",
        "ingest_preanonymized": "ingest_preanonymized",
        "managed_anonymized_videos": "managed_anonymized_videos",
        "managed_anonymized_reports": "managed_anonymized_reports",
        "managed_sensitive_sidecars": "managed_sensitive_sidecars",
        "quarantine_failed": "quarantine_failed",
        "staging_migration": "staging_migration",
        "test": "test",
    }

    @classmethod
    def from_environment(cls) -> "EndoregPathsModel":
        protected_root = _resolve_protected_root()
        data_dir = _resolve_data_root()
        storage_dir = _resolve_protected_subdir(
            env_key=STORAGE_DIR_ENV,
            default_path=protected_root / "storage",
            protected_root=protected_root,
        )
        export_dir = data_dir / EXPORT_DIR_NAME
        import_dir = data_dir / IMPORT_DIR_NAME
        logs_dir = data_dir / LOG_DIR_NAME
        quarantine_dir = data_dir / QUARANTINE_DIR_NAME
        migration_staging_dir = data_dir / MIGRATION_STAGING_DIR_NAME
        manifest_dir = migration_staging_dir / MANIFEST_DIR_NAME
        upload_api_dir = storage_dir / "upload_jobs" / "api"
        upload_watcher_dir = storage_dir / "upload_jobs" / "watcher"
        upload_preanonymized_dir = storage_dir / "upload_jobs" / "preanonymized"
        ingest_uploads_dir = storage_dir / "upload_jobs"
        ingest_preanonymized_dir = import_dir / PREANONYMIZED_IMPORT_DIR_NAME
        managed_sensitive_sidecars_dir = storage_dir / "sensitive_sidecars"
        quarantine_failed_dir = quarantine_dir / "failed"
        watcher_video_drop_dir = import_dir / IMPORT_VIDEO_DIR_NAME
        watcher_report_drop_dir = import_dir / REPORT_IMPORT_DIR_NAME
        watcher_preanonymized_drop_dir = import_dir / PREANONYMIZED_IMPORT_DIR_NAME
        sap_import_drop_dir = import_dir / "sap_import"
        sap_import_processed_dir = import_dir / "sap_import_processed"
        sap_import_failed_dir = import_dir / "sap_import_failed"

        instance = cls(
            dir=storage_dir,
            dirs=[
                protected_root,
                data_dir,
                storage_dir,
                import_dir,
                export_dir,
                logs_dir,
                quarantine_dir,
                migration_staging_dir,
                manifest_dir,
                upload_api_dir,
                upload_watcher_dir,
                upload_preanonymized_dir,
                ingest_uploads_dir,
                ingest_preanonymized_dir,
                managed_sensitive_sidecars_dir,
                quarantine_failed_dir,
                watcher_video_drop_dir,
                watcher_report_drop_dir,
                watcher_preanonymized_drop_dir,
                sap_import_drop_dir,
                sap_import_processed_dir,
                sap_import_failed_dir,
                import_dir / IMPORT_VIDEO_DIR_NAME,
                import_dir / REPORT_IMPORT_DIR_NAME,
                import_dir / PREANONYMIZED_IMPORT_DIR_NAME,
                import_dir / ANONYMIZED_VIDEO_IMPORT_DIR_NAME,
                import_dir / ANONYMIZED_REPORT_IMPORT_DIR_NAME,
                export_dir / VIDEO_EXPORT_DIR_NAME,
                export_dir / REPORT_EXPORT_DIR_NAME,
                storage_dir / "documents",
                storage_dir / "temp",
                storage_dir / SENSITIVE_VIDEO_DIR_NAME,
                storage_dir / SENSITIVE_REPORT_DIR_NAME,
                storage_dir / ANONYM_VIDEO_DIR_NAME,
                storage_dir / ANONYM_REPORT_DIR_NAME,
                storage_dir / RAW_FRAME_DIR_NAME,
                storage_dir / FRAME_DIR_NAME,
                storage_dir / WEIGHTS_DIR_NAME,
                import_dir / WEIGHTS_DIR_NAME,
                export_dir / WEIGHTS_DIR_NAME,
                import_dir / FRAME_DIR_NAME,
                export_dir / FRAME_DIR_NAME,
                storage_dir / "test",
            ],
            protected_root=protected_root,
            storage=storage_dir,
            data=data_dir,
            import_dir=import_dir,
            export_dir=export_dir,
            import_video=import_dir / IMPORT_VIDEO_DIR_NAME,
            import_report=import_dir / REPORT_IMPORT_DIR_NAME,
            import_preanonymized=import_dir / PREANONYMIZED_IMPORT_DIR_NAME,
            import_anonymized_video=import_dir / ANONYMIZED_VIDEO_IMPORT_DIR_NAME,
            import_anonymized_report=import_dir / ANONYMIZED_REPORT_IMPORT_DIR_NAME,
            video_export=export_dir / VIDEO_EXPORT_DIR_NAME,
            report_export=export_dir / REPORT_EXPORT_DIR_NAME,
            documents=storage_dir / "documents",
            transcoding=storage_dir / "temp",
            sensitive_video=storage_dir / SENSITIVE_VIDEO_DIR_NAME,
            sensitive_report=storage_dir / SENSITIVE_REPORT_DIR_NAME,
            anonym_video=storage_dir / ANONYM_VIDEO_DIR_NAME,
            anonym_report=storage_dir / ANONYM_REPORT_DIR_NAME,
            raw_frame=storage_dir / RAW_FRAME_DIR_NAME,
            frame=storage_dir / FRAME_DIR_NAME,
            weights=storage_dir / WEIGHTS_DIR_NAME,
            weights_import=import_dir / WEIGHTS_DIR_NAME,
            weights_export=export_dir / WEIGHTS_DIR_NAME,
            import_frame=import_dir / FRAME_DIR_NAME,
            frame_export=export_dir / FRAME_DIR_NAME,
            logs=logs_dir,
            quarantine=quarantine_dir,
            migration_staging=migration_staging_dir,
            manifest_dir=manifest_dir,
            upload_api=upload_api_dir,
            upload_watcher=upload_watcher_dir,
            upload_preanonymized=upload_preanonymized_dir,
            watcher_video_drop=watcher_video_drop_dir,
            watcher_report_drop=watcher_report_drop_dir,
            watcher_preanonymized_drop=watcher_preanonymized_drop_dir,
            sap_import_drop=sap_import_drop_dir,
            sap_import_processed=sap_import_processed_dir,
            sap_import_failed=sap_import_failed_dir,
            ingest_uploads=ingest_uploads_dir,
            ingest_preanonymized=ingest_preanonymized_dir,
            managed_anonymized_videos=storage_dir / ANONYM_VIDEO_DIR_NAME,
            managed_anonymized_reports=storage_dir / ANONYM_REPORT_DIR_NAME,
            managed_sensitive_sidecars=managed_sensitive_sidecars_dir,
            quarantine_failed=quarantine_failed_dir,
            staging_migration=migration_staging_dir,
            test=storage_dir / "test",
        )
        instance.ensure_directories()
        return instance

    def ensure_directories(self) -> None:
        for path in self.dirs:
            path.mkdir(parents=True, exist_ok=True)

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


EndoregPathsModel.model_rebuild()

data_paths_model = EndoregPathsModel.from_environment()
data_paths = data_paths_model

PROTECTED_DATA_ROOT = data_paths_model.protected_root
DATA_DIR = data_paths_model.data
STORAGE_DIR = data_paths_model.storage

IMPORT_DIR = data_paths_model.import_dir
EXPORT_DIR = data_paths_model.export_dir

IMPORT_VIDEO_DIR = data_paths_model.import_video
IMPORT_REPORT_DIR = data_paths_model.import_report
IMPORT_PREANONYMIZED_DIR = data_paths_model.import_preanonymized
IMPORT_ANONYMIZED_VIDEO_DIR = data_paths_model.import_anonymized_video
IMPORT_ANONYMIZED_REPORT_DIR = data_paths_model.import_anonymized_report

VIDEO_EXPORT_DIR = data_paths_model.video_export
REPORT_EXPORT_DIR = data_paths_model.report_export

DOCUMENT_DIR = data_paths_model.documents
TRANSCODING_DIR = data_paths_model.transcoding

ANONYM_VIDEO_DIR = data_paths_model.anonym_video
SENSITIVE_VIDEO_DIR = data_paths_model.sensitive_video
ANONYM_REPORT_DIR = data_paths_model.anonym_report
SENSITIVE_REPORT_DIR = data_paths_model.sensitive_report

FRAME_DIR = data_paths_model.frame
WEIGHTS_DIR = data_paths_model.weights
RAW_FRAME_DIR = data_paths_model.raw_frame

WEIGHTS_IMPORT_DIR = data_paths_model.weights_import
WEIGHTS_EXPORT_DIR = data_paths_model.weights_export

FRAME_IMPORT_DIR = data_paths_model.import_frame
FRAME_EXPORT_DIR = data_paths_model.frame_export

LOG_DIR = data_paths_model.logs
QUARANTINE_DIR = data_paths_model.quarantine
MIGRATION_STAGING_DIR = data_paths_model.migration_staging
MANIFEST_DIR = data_paths_model.manifest_dir
UPLOAD_API_DIR = data_paths_model.upload_api
UPLOAD_WATCHER_DIR = data_paths_model.upload_watcher
UPLOAD_PREANONYMIZED_DIR = data_paths_model.upload_preanonymized
WATCHER_VIDEO_DROP_DIR = data_paths_model.watcher_video_drop
WATCHER_REPORT_DROP_DIR = data_paths_model.watcher_report_drop
WATCHER_PREANONYMIZED_DROP_DIR = data_paths_model.watcher_preanonymized_drop
SAP_IMPORT_DROP_DIR = data_paths_model.sap_import_drop
SAP_IMPORT_PROCESSED_DIR = data_paths_model.sap_import_processed
SAP_IMPORT_FAILED_DIR = data_paths_model.sap_import_failed
INGEST_UPLOADS_DIR = data_paths_model.ingest_uploads
INGEST_PREANONYMIZED_DIR = data_paths_model.ingest_preanonymized
MANAGED_ANONYMIZED_VIDEOS_DIR = data_paths_model.managed_anonymized_videos
MANAGED_ANONYMIZED_REPORTS_DIR = data_paths_model.managed_anonymized_reports
MANAGED_SENSITIVE_SIDECARS_DIR = data_paths_model.managed_sensitive_sidecars
QUARANTINE_FAILED_DIR = data_paths_model.quarantine_failed
STAGING_MIGRATION_DIR = data_paths_model.staging_migration

logger.debug("Protected data root: %s", PROTECTED_DATA_ROOT.resolve())
logger.debug("Data directory: %s", DATA_DIR.resolve())
logger.debug("Encrypted storage directory: %s", STORAGE_DIR.resolve())
logger.debug("Export directory: %s", EXPORT_DIR.resolve())


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
    current_storage_root = current_paths.storage.resolve()
    storage_roots = [current_storage_root]
    if TEST_PATH_COMPAT_ENABLED:
        legacy_storage_root = (BASE_DIR / "data" / "tests" / "storage").resolve()
        if legacy_storage_root not in storage_roots:
            storage_roots.append(legacy_storage_root)

    for storage_root in storage_roots:
        try:
            relative_path = resolved_path.relative_to(storage_root)
            return relative_path.as_posix()
        except ValueError:
            continue

    data_roots = [current_paths.data.resolve()]
    if TEST_PATH_COMPAT_ENABLED:
        legacy_data_roots = [
            TEST_DATA_ROOT.resolve(),
            (BASE_DIR / "data" / "tests" / "storage").resolve(),
        ]
        for data_root in legacy_data_roots:
            if data_root not in data_roots:
                data_roots.append(data_root)

    for data_root in data_roots:
        try:
            return resolved_path.relative_to(data_root).as_posix()
        except ValueError:
            continue

    ensure_within_protected_root(resolved_path)
    return original_path


def to_protected_relative(path: str | Path) -> str:
    return (
        ensure_within_protected_root(path)
        .relative_to(EndoregPathsModel.from_environment().protected_root.resolve())
        .as_posix()
    )


def get_storage_tier_root(tier: str) -> Path:
    current_paths = EndoregPathsModel.from_environment()
    mapping = {
        "upload_api": current_paths.upload_api,
        "upload_watcher": current_paths.upload_watcher,
        "upload_preanonymized": current_paths.upload_preanonymized,
        "ingest_uploads": current_paths.ingest_uploads,
        "ingest_preanonymized": current_paths.ingest_preanonymized,
        "managed_anonymized_videos": current_paths.managed_anonymized_videos,
        "managed_anonymized_reports": current_paths.managed_anonymized_reports,
        "managed_sensitive_sidecars": current_paths.managed_sensitive_sidecars,
        "watcher_video_drop": current_paths.watcher_video_drop,
        "watcher_report_drop": current_paths.watcher_report_drop,
        "watcher_preanonymized_drop": current_paths.watcher_preanonymized_drop,
        "sap_import_drop": current_paths.sap_import_drop,
        "sap_import_processed": current_paths.sap_import_processed,
        "sap_import_failed": current_paths.sap_import_failed,
        "manifest": current_paths.manifest_dir,
        "migration_staging": current_paths.migration_staging,
        "staging_migration": current_paths.staging_migration,
        "quarantine": current_paths.quarantine,
        "quarantine_failed": current_paths.quarantine_failed,
    }
    try:
        return mapping[tier]
    except KeyError as exc:
        raise KeyError(f"Unknown storage tier: {tier}") from exc


def validate_runtime_storage_contract() -> None:
    protected_root_env = os.environ.get(PROTECTED_ROOT_ENV, "").strip()
    django_env = os.environ.get("DJANGO_ENV", "").strip().lower()
    is_production = django_env == "production"

    if not protected_root_env:
        raise RuntimeError(
            f"{PROTECTED_ROOT_ENV} must be set for the protected runtime contract."
        )

    protected_paths_to_validate = {
        "protected_root": PROTECTED_DATA_ROOT,
        "storage": STORAGE_DIR,
        "upload_api": UPLOAD_API_DIR,
        "upload_watcher": UPLOAD_WATCHER_DIR,
        "upload_preanonymized": UPLOAD_PREANONYMIZED_DIR,
    }
    public_paths_to_validate = {
        "data_root": DATA_DIR,
        "import": IMPORT_DIR,
        "export": EXPORT_DIR,
        "logs": LOG_DIR,
        "quarantine": QUARANTINE_DIR,
        "migration_staging": MIGRATION_STAGING_DIR,
        "manifest": MANIFEST_DIR,
        "watcher_video_drop": WATCHER_VIDEO_DROP_DIR,
        "watcher_report_drop": WATCHER_REPORT_DROP_DIR,
        "watcher_preanonymized_drop": WATCHER_PREANONYMIZED_DROP_DIR,
        "sap_import_drop": SAP_IMPORT_DROP_DIR,
        "sap_import_processed": SAP_IMPORT_PROCESSED_DIR,
        "sap_import_failed": SAP_IMPORT_FAILED_DIR,
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
                path.mkdir(parents=True, exist_ok=True)
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


def resolve_storage_tier_path(tier: str, *parts: str | Path) -> Path:
    root = get_storage_tier_root(tier)
    candidate = root.joinpath(*[str(part) for part in parts]).resolve()
    protected_tiers = {
        "upload_api",
        "upload_watcher",
        "upload_preanonymized",
        "ingest_uploads",
        "managed_anonymized_videos",
        "managed_anonymized_reports",
        "managed_sensitive_sidecars",
    }
    if tier in protected_tiers:
        return ensure_within_protected_root(candidate)
    return ensure_within_data_root(candidate)


def build_upload_job_relative_path(*, tier: str, filename: str, key: str) -> str:
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
