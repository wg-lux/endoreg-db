"""
Centralized path management for the application.

The module exposes the historical path constants plus a dict-like ``data_paths``
mapping, but uses a Pydantic model as the single source of truth so path
resolution and directory bootstrap stay consistent.
"""

from __future__ import annotations

from collections.abc import Iterable
import os
from logging import getLogger
from pathlib import Path
from typing import ClassVar

from endoreg_db.config.env import BASE_DIR, env_path
from lx_dtypes.models.base.file.pydantic.FilesAndDirs import FilesAndDirsModel

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

PROTECTED_ROOT_ENV = "LX_ANNOTATE_ENCRYPTED_DATA_DIR"
LEGACY_STORAGE_ENV = "STORAGE_DIR"
LEGACY_IO_ENV = "IO_DIR"


def _resolve_env_path(raw_value: str) -> Path:
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate.resolve()
    return (BASE_DIR / candidate).resolve()


def _resolve_protected_root() -> Path:
    return env_path(PROTECTED_ROOT_ENV, "data").resolve()


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
    protected_root = PROTECTED_DATA_ROOT.resolve()
    try:
        resolved_path.relative_to(protected_root)
    except ValueError as exc:
        raise ValueError(
            f"Path {resolved_path} is outside protected data root {protected_root}"
        ) from exc
    return resolved_path


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
    io: Path
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

    # If any directory names change, please ensure continued support by changing the values  in key: value.
    legacy_key_map: ClassVar[dict[str, str]] = {
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
    }

    @classmethod
    def from_environment(cls) -> "EndoregPathsModel":
        protected_root = _resolve_protected_root()
        storage_dir = _resolve_protected_subdir(
            env_key=LEGACY_STORAGE_ENV,
            default_path=protected_root / "storage",
            protected_root=protected_root,
        )
        io_dir = _resolve_protected_subdir(
            env_key=LEGACY_IO_ENV,
            default_path=protected_root,
            protected_root=protected_root,
        )

        import_dir = io_dir / IMPORT_DIR_NAME
        export_dir = io_dir / EXPORT_DIR_NAME
        logs_dir = io_dir / LOG_DIR_NAME
        quarantine_dir = io_dir / QUARANTINE_DIR_NAME
        migration_staging_dir = io_dir / MIGRATION_STAGING_DIR_NAME
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
                storage_dir,
                io_dir,
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
            ],
            protected_root=protected_root,
            storage=storage_dir,
            io=io_dir,
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
        )
        instance.ensure_directories()
        return instance

    def ensure_directories(self) -> None:
        for path in self.dirs:
            path.mkdir(parents=True, exist_ok=True)
            logger.info("Path ready: %s", path.resolve())

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
STORAGE_DIR = data_paths_model.storage
IO_DIR = data_paths_model.io

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

logger.info("Protected data root: %s", PROTECTED_DATA_ROOT.resolve())
logger.info("Storage directory: %s", STORAGE_DIR.resolve())
logger.info("Export directory: %s", EXPORT_DIR.resolve())


def to_storage_relative(path: str | Path) -> str:
    """
    Return a path string relative to STORAGE_DIR, suitable for Django FileField.name.

    If ``path`` is outside STORAGE_DIR, it is returned unchanged.
    """
    original_path = str(path)
    resolved_path = ensure_within_protected_root(path)
    storage_root = STORAGE_DIR.resolve()

    try:
        relative_path = resolved_path.relative_to(storage_root)
    except ValueError:
        return original_path

    return relative_path.as_posix()


def to_protected_relative(path: str | Path) -> str:
    return (
        ensure_within_protected_root(path)
        .relative_to(PROTECTED_DATA_ROOT.resolve())
        .as_posix()
    )


def get_storage_tier_root(tier: str) -> Path:
    mapping = {
        "upload_api": UPLOAD_API_DIR,
        "upload_watcher": UPLOAD_WATCHER_DIR,
        "upload_preanonymized": UPLOAD_PREANONYMIZED_DIR,
        "ingest_uploads": INGEST_UPLOADS_DIR,
        "ingest_preanonymized": INGEST_PREANONYMIZED_DIR,
        "managed_anonymized_videos": MANAGED_ANONYMIZED_VIDEOS_DIR,
        "managed_anonymized_reports": MANAGED_ANONYMIZED_REPORTS_DIR,
        "managed_sensitive_sidecars": MANAGED_SENSITIVE_SIDECARS_DIR,
        "watcher_video_drop": WATCHER_VIDEO_DROP_DIR,
        "watcher_report_drop": WATCHER_REPORT_DROP_DIR,
        "watcher_preanonymized_drop": WATCHER_PREANONYMIZED_DROP_DIR,
        "sap_import_drop": SAP_IMPORT_DROP_DIR,
        "sap_import_processed": SAP_IMPORT_PROCESSED_DIR,
        "sap_import_failed": SAP_IMPORT_FAILED_DIR,
        "manifest": MANIFEST_DIR,
        "migration_staging": MIGRATION_STAGING_DIR,
        "staging_migration": STAGING_MIGRATION_DIR,
        "quarantine": QUARANTINE_DIR,
        "quarantine_failed": QUARANTINE_FAILED_DIR,
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

    paths_to_validate = {
        "protected_root": PROTECTED_DATA_ROOT,
        "storage": STORAGE_DIR,
        "io": IO_DIR,
        "logs": LOG_DIR,
        "quarantine": QUARANTINE_DIR,
        "migration_staging": MIGRATION_STAGING_DIR,
        "manifest": MANIFEST_DIR,
        "upload_api": UPLOAD_API_DIR,
        "upload_watcher": UPLOAD_WATCHER_DIR,
        "upload_preanonymized": UPLOAD_PREANONYMIZED_DIR,
        "watcher_video_drop": WATCHER_VIDEO_DROP_DIR,
        "watcher_report_drop": WATCHER_REPORT_DROP_DIR,
        "watcher_preanonymized_drop": WATCHER_PREANONYMIZED_DROP_DIR,
        "sap_import_drop": SAP_IMPORT_DROP_DIR,
        "sap_import_processed": SAP_IMPORT_PROCESSED_DIR,
        "sap_import_failed": SAP_IMPORT_FAILED_DIR,
    }
    for label, path in paths_to_validate.items():
        try:
            ensure_within_protected_root(path)
        except ValueError as exc:
            raise RuntimeError(
                f"Runtime storage contract invalid for {label}: {exc}"
            ) from exc
        if not path.exists():
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
    return ensure_within_protected_root(candidate)


def build_upload_job_relative_path(*, tier: str, filename: str, key: str) -> str:
    sanitized_name = Path(filename).name or "upload.bin"
    relative_path = resolve_storage_tier_path(
        tier,
        key[:2] or "00",
        key,
        sanitized_name,
    ).relative_to(STORAGE_DIR.resolve())
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
        return ensure_within_protected_root(fallback)
    return ensure_within_protected_root(_resolve_env_path(str(raw_path)))
