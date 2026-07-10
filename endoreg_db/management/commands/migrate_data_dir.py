from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import NoneType
from typing import Protocol, Sequence, TypeAlias, TypedDict, Unpack, cast

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models.fields.files import FieldFile
from django.db.models import Q
from lx_dtypes.models.contracts.migrate_data_dir import (
    MigrateDataDirCommandOptionsPayload,
    MigrateDataDirManifestEntryPayload,
    MigrateDataDirManifestPayload,
)

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
    sha256_file,
)
from endoreg_db.utils.paths import (
    DOCUMENT_DIR,
    FRAME_DIR,
    FRAME_IMPORT_DIR,
    IMPORT_ANONYMIZED_REPORT_DIR,
    IMPORT_ANONYMIZED_VIDEO_DIR,
    MANAGED_ANONYMIZED_REPORTS_DIR,
    MANAGED_ANONYMIZED_VIDEOS_DIR,
    MANAGED_SENSITIVE_SIDECARS_DIR,
    RAW_FRAME_DIR,
    SAP_IMPORT_DROP_DIR,
    SAP_IMPORT_FAILED_DIR,
    SAP_IMPORT_PROCESSED_DIR,
    SENSITIVE_REPORT_DIR,
    SENSITIVE_VIDEO_DIR,
    WATCHER_PREANONYMIZED_DROP_DIR,
    WATCHER_REPORT_DROP_DIR,
    WATCHER_VIDEO_DROP_DIR,
    WEIGHTS_DIR,
    WEIGHTS_IMPORT_DIR,
    build_manifest_path,
    build_upload_job_relative_path,
    ensure_within_data_root,
    ensure_within_protected_root,
    to_storage_relative,
)
from endoreg_db.utils.storage_profile import (
    PayloadKind,
    requires_app_encrypted_storage,
)
from endoreg_db.utils.storage import save_local_file

logger = logging.getLogger(__name__)


class MigrateDataDirCommandOptions(TypedDict):
    source_root: str
    dry_run: bool
    manifest_path: str


def _migrate_data_dir_command_options_payload(
    options: dict[str, object],
) -> MigrateDataDirCommandOptionsPayload:
    return MigrateDataDirCommandOptionsPayload.model_validate(
        {
            "source_root": options.get("source_root"),
            "dry_run": options.get("dry_run"),
            "manifest_path": options.get("manifest_path", ""),
        }
    )


class PersistedMigrationModel(Protocol):
    id: int
    pk: int

    def save(self, *, update_fields: Sequence[str]) -> None: ...


class _MigrationStorage(Protocol):
    def exists(self, name: str) -> bool: ...


class _MigrationEncryptedStorage(_MigrationStorage, Protocol):
    def is_encrypted(self, name: str) -> bool: ...


class _MigrationFileField(Protocol):
    upload_to: str

    def generate_filename(
        self, instance: PersistedMigrationModel, filename: str
    ) -> str: ...


class _MigrationFieldFile(Protocol):
    name: str
    storage: _MigrationStorage
    field: _MigrationFileField


NoMigrationValue: TypeAlias = NoneType
MigrationFieldValue: TypeAlias = str | VideoStorageMode
MigrationExtensions: TypeAlias = tuple[str, ...] | NoMigrationValue


def _ensure_within_runtime_root(path: Path) -> Path:
    try:
        return ensure_within_protected_root(path)
    except ValueError:
        return ensure_within_data_root(path)


def _streamable_video_root() -> Path:
    return ensure_within_protected_root(
        Path(
            os.environ.get(
                "LX_ANNOTATE_STREAMABLE_VIDEO_ROOT",
                str(MANAGED_ANONYMIZED_VIDEOS_DIR.parent / "streamable_videos"),
            )
        ).expanduser()
    )


def _streamable_raw_video_root() -> Path:
    return ensure_within_protected_root(
        Path(
            os.environ.get(
                "LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT",
                str(_streamable_video_root() / "raw"),
            )
        ).expanduser()
    )


def _streamable_processed_video_root() -> Path:
    return ensure_within_protected_root(
        Path(
            os.environ.get(
                "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT",
                str(_streamable_video_root() / "processed"),
            )
        ).expanduser()
    )


@dataclass(frozen=True, slots=True)
class MigrationRule:
    legacy_relative: Path
    target_root: Path
    create_upload_job: bool
    storage_class: str
    storage_tier: str
    retention_policy: str
    source_file_persisted: bool
    cleanup_status: str
    allowed_extensions: MigrationExtensions = None


VIDEO_FILE_EXTENSIONS = (".mp4", ".webm", ".avi", ".mkv", ".mov", ".m4v")
REPORT_FILE_EXTENSIONS = (".pdf", ".txt")
FRAME_FILE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".webp",
    ".json",
    ".csv",
    ".txt",
)


MIGRATION_RULES: tuple[MigrationRule, ...] = (
    MigrationRule(
        legacy_relative=Path("import/video_import"),
        target_root=WATCHER_VIDEO_DROP_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("import/report_import"),
        target_root=WATCHER_REPORT_DROP_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        allowed_extensions=REPORT_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("import/preanonymized_import"),
        target_root=WATCHER_PREANONYMIZED_DROP_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        allowed_extensions=REPORT_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("import/anonymized_video_import"),
        target_root=IMPORT_ANONYMIZED_VIDEO_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("import/anonymized_report_import"),
        target_root=IMPORT_ANONYMIZED_REPORT_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=REPORT_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("import/frames"),
        target_root=FRAME_IMPORT_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=FRAME_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("import/model_weights"),
        target_root=WEIGHTS_IMPORT_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("raw_frames"),
        target_root=RAW_FRAME_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=FRAME_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("frames"),
        target_root=FRAME_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=FRAME_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("model_weights"),
        target_root=WEIGHTS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("documents"),
        target_root=DOCUMENT_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("sensitive_sidecars"),
        target_root=MANAGED_SENSITIVE_SIDECARS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier="managed_sensitive_sidecars",
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("import/sap_import"),
        target_root=SAP_IMPORT_DROP_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("import/sap_import_processed"),
        target_root=SAP_IMPORT_PROCESSED_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("import/sap_import_failed"),
        target_root=SAP_IMPORT_FAILED_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.QUARANTINE,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("sensitive_videos"),
        target_root=SENSITIVE_VIDEO_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("sensitive_reports"),
        target_root=SENSITIVE_REPORT_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=REPORT_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/sensitive_videos"),
        target_root=SENSITIVE_VIDEO_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/sensitive_reports"),
        target_root=SENSITIVE_REPORT_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=REPORT_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("processed_videos_final"),
        target_root=MANAGED_ANONYMIZED_VIDEOS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("processed_reports_final"),
        target_root=MANAGED_ANONYMIZED_REPORTS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=REPORT_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/processed_videos_final"),
        target_root=MANAGED_ANONYMIZED_VIDEOS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/processed_reports_final"),
        target_root=MANAGED_ANONYMIZED_REPORTS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=REPORT_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("streamable_videos/raw"),
        target_root=_streamable_raw_video_root(),
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("streamable_videos/processed"),
        target_root=_streamable_processed_video_root(),
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/streamable_videos/raw"),
        target_root=_streamable_raw_video_root(),
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/streamable_videos/processed"),
        target_root=_streamable_processed_video_root(),
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=VIDEO_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/raw_frames"),
        target_root=RAW_FRAME_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=FRAME_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/frames"),
        target_root=FRAME_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=FRAME_FILE_EXTENSIONS,
    ),
    MigrationRule(
        legacy_relative=Path("storage/model_weights"),
        target_root=WEIGHTS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("storage/documents"),
        target_root=DOCUMENT_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
    MigrationRule(
        legacy_relative=Path("storage/sensitive_sidecars"),
        target_root=MANAGED_SENSITIVE_SIDECARS_DIR,
        create_upload_job=False,
        storage_class=UploadJob.StorageClass.MANAGED,
        storage_tier="managed_sensitive_sidecars",
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.SKIPPED,
        allowed_extensions=None,
    ),
)


class Command(BaseCommand):
    help = (
        "Migrate a legacy data directory into the protected storage tiers under "
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR and emit a JSON manifest."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "source_root",
            type=str,
            help="Legacy data root to translate into the protected storage tiers.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned migrations without copying files or writing UploadJob rows.",
        )
        parser.add_argument(
            "--manifest-path",
            type=str,
            default="",
            help="Manifest path inside the protected manifest tier.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[MigrateDataDirCommandOptions],
    ) -> None:
        options_payload = _migrate_data_dir_command_options_payload(
            cast(dict[str, object], options)
        )
        source_root = Path(options_payload.source_root).expanduser().resolve()
        if not source_root.exists() or not source_root.is_dir():
            raise CommandError(f"Legacy source root does not exist: {source_root}")

        dry_run = options_payload.dry_run
        manifest_path = self._resolve_manifest_path(
            raw_path=options_payload.manifest_path.strip(),
            source_root=source_root,
        )

        migrated_entries: list[MigrateDataDirManifestEntryPayload] = []
        skipped_entries: list[MigrateDataDirManifestEntryPayload] = []

        for rule in MIGRATION_RULES:
            legacy_dir = source_root / rule.legacy_relative
            if not legacy_dir.exists():
                continue
            for source_path in sorted(legacy_dir.rglob("*")):
                if not source_path.is_file():
                    continue
                destination_path = _ensure_within_runtime_root(
                    rule.target_root / source_path.relative_to(legacy_dir)
                )
                entry = MigrateDataDirManifestEntryPayload(
                    source_path=str(source_path),
                    destination_path=str(destination_path),
                    storage_class=rule.storage_class,
                    storage_tier=rule.storage_tier,
                    retention_policy=rule.retention_policy,
                    create_upload_job=rule.create_upload_job,
                )
                # FIX 1: Ignore lock files
                if source_path.suffix.lower() == ".lock":
                    logger.warning(f"Skipping lock file: {source_path}")
                    continue

                # FIX 2: Ignore non-media junk.
                allowed_extensions = rule.allowed_extensions
                if (
                    allowed_extensions is not None
                    and source_path.suffix.lower() not in allowed_extensions
                ):
                    logger.warning(f"Skipping unsupported file type: {source_path}")
                    continue

                if self._raw_streamable_disabled(rule):
                    skipped_entries.append(
                        entry.model_copy(
                            update={"reason": "raw_streamable_disabled_by_policy"}
                        )
                    )
                    logger.warning(
                        "Skipping raw streamable migration because raw video policy "
                        "requires application-encrypted storage: source=%s "
                        "destination=%s",
                        source_path,
                        destination_path,
                    )
                    continue

                if dry_run:
                    migrated_entries.append(
                        entry.model_copy(
                            update={
                                "dry_run": True,
                                "destination_exists": destination_path.exists(),
                            }
                        )
                    )
                    continue

                # 2. If it already exists at the destination, run the "Database Sync" logic
                if destination_path.exists():
                    source_hash = sha256_file(source_path)
                    destination_hash = sha256_file(destination_path)
                    if source_hash != destination_hash:
                        logger.error(
                            "Refusing to sync migration destination with mismatched "
                            "content hash: source=%s destination=%s source_sha256=%s "
                            "destination_sha256=%s",
                            source_path,
                            destination_path,
                            source_hash,
                            destination_hash,
                        )
                        skipped_entries.append(
                            entry.model_copy(
                                update={
                                    "reason": "destination_exists_hash_mismatch",
                                    "source_sha256": source_hash,
                                    "destination_sha256": destination_hash,
                                }
                            )
                        )
                        continue

                    synced = self.sync_db(destination_path, source_path, rule)
                    skipped_entries.append(
                        entry.model_copy(
                            update={
                                "reason": (
                                    "destination_exists_and_synced"
                                    if synced
                                    else "destination_exists_unchanged"
                                )
                            }
                        )
                    )
                    continue

                atomic_copy_file(
                    source=source_path,
                    destination=destination_path,
                    preserve_metadata=True,
                )
                job_id = None
                if rule.create_upload_job:
                    job = self._create_or_reuse_migration_upload_job(
                        migrated_file=destination_path,
                        source_path=source_path,
                        rule=rule,
                    )
                    job_id = str(cast("PersistedMigrationModel", job).id)
                if self.sync_db(destination_path, source_path, rule):
                    logger.info(
                        f"synced db path of {destination_path} to {source_path}"
                    )
                else:
                    logger.info(
                        f"No existing DB records found for {source_path.name} to sync."
                    )
                migrated_entries.append(
                    entry.model_copy(update={"upload_job_id": job_id})
                )

        manifest = MigrateDataDirManifestPayload(
            command="migrate_data_dir",
            created_at=datetime.now(timezone.utc).isoformat(),
            source_root=str(source_root),
            dry_run=dry_run,
            migrated_entries=migrated_entries,
            skipped_entries=skipped_entries,
        ).to_manifest_data()
        ensure_directory(manifest_path.parent)
        manifest_payload = json.dumps(manifest, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        atomic_write_file(
            destination=manifest_path,
            content=[manifest_payload],
            required_bytes=len(manifest_payload),
        )
        self.stdout.write(f"Manifest written to {manifest_path}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Migrated {len(migrated_entries)} file(s); skipped {len(skipped_entries)}."
            )
        )

    def sync_db(
        self, destination_path: Path, source_path: Path, rule: MigrationRule
    ) -> bool:
        self.stdout.write(
            self.style.NOTICE(f"Syncing DB for existing file: {source_path.name}")
        )

        # Calculate content hash and derive the canonical hash stem from the filename.
        # Processed media is typically named after the raw media hash, not the
        # processed bytes hash, so we use both signals and fail closed on ambiguity.
        content_hash = sha256_file(destination_path)
        stem_hash = self._canonical_hash_stem(destination_path)
        rel_path = to_storage_relative(destination_path)
        updated_count = 0
        # Re-point existing Video records
        if rule.target_root in {
            MANAGED_ANONYMIZED_VIDEOS_DIR,
            IMPORT_ANONYMIZED_VIDEO_DIR,
        }:
            video = self._resolve_unique_video(
                content_hash=content_hash,
                stem_hash=stem_hash,
                include_processed_hash=True,
                source_path=source_path,
                destination_path=destination_path,
            )
            if video is not None:
                processed_video_hash = getattr(video, "processed_video_hash", None)
                processed_hash_mismatch = bool(
                    processed_video_hash and processed_video_hash != content_hash
                )
                file_changed = self._store_file_field_if_needed(
                    instance=cast(PersistedMigrationModel, video),
                    field_name="processed_file",
                    relative_name=rel_path,
                    source_path=source_path,
                    payload_kind=PayloadKind.VIDEO_PROCESSED,
                    force_upload_to=processed_hash_mismatch,
                )
                video_update_fields: dict[str, MigrationFieldValue] = {}
                if not processed_video_hash:
                    video_update_fields["processed_video_hash"] = content_hash
                elif processed_hash_mismatch:
                    logger.warning(
                        "Processed video hash mismatch during migration sync for "
                        "video=%s source=%s destination=%s existing=%s incoming=%s. "
                        "Leaving processed_video_hash unchanged.",
                        video.pk,
                        source_path,
                        destination_path,
                        processed_video_hash,
                        content_hash,
                    )
                extra_changed = self._changed_fields(
                    cast(PersistedMigrationModel, video), video_update_fields
                )
                for field_name, desired_value in extra_changed.items():
                    setattr(video, field_name, desired_value)
                if file_changed or extra_changed:
                    update_fields: list[str] = [
                        *(["processed_file"] if file_changed else []),
                        *extra_changed.keys(),
                    ]
                    cast("PersistedMigrationModel", video).save(
                        update_fields=update_fields
                    )
                    updated_count = 1
        elif rule.target_root == SENSITIVE_VIDEO_DIR:
            video = self._resolve_unique_video(
                content_hash=content_hash,
                stem_hash=stem_hash,
                include_processed_hash=False,
                source_path=source_path,
                destination_path=destination_path,
            )
            if video is not None:
                changed = self._store_file_field_if_needed(
                    instance=cast(PersistedMigrationModel, video),
                    field_name="raw_file",
                    relative_name=rel_path,
                    source_path=source_path,
                    payload_kind=PayloadKind.VIDEO_RAW,
                )
                if changed:
                    cast("PersistedMigrationModel", video).save(
                        update_fields=["raw_file"]
                    )
                    updated_count = 1
        elif rule.target_root == _streamable_raw_video_root():
            if self._raw_streamable_disabled(rule):
                logger.warning(
                    "Refusing to sync raw streamable path because raw video policy "
                    "requires application-encrypted storage: source=%s destination=%s",
                    source_path,
                    destination_path,
                )
                return False
            video = self._resolve_unique_video(
                content_hash=content_hash,
                stem_hash=stem_hash,
                include_processed_hash=False,
                source_path=source_path,
                destination_path=destination_path,
            )
            if video is not None:
                updated_count = self._update_video_if_changed(
                    video,
                    raw_streamable_relative_path=rel_path,
                    storage_mode=VideoStorageMode.STREAMABLE,
                )
        elif rule.target_root == _streamable_processed_video_root():
            video = self._resolve_unique_video(
                content_hash=content_hash,
                stem_hash=stem_hash,
                include_processed_hash=True,
                source_path=source_path,
                destination_path=destination_path,
            )
            if video is not None:
                updated_count = self._update_video_if_changed(
                    video,
                    processed_streamable_relative_path=rel_path,
                    storage_mode=VideoStorageMode.STREAMABLE,
                )

        # Re-point existing Report records
        elif rule.target_root in {
            MANAGED_ANONYMIZED_REPORTS_DIR,
            IMPORT_ANONYMIZED_REPORT_DIR,
        }:
            report = self._resolve_unique_report(
                content_hash=content_hash,
                stem_hash=stem_hash,
                source_path=source_path,
                destination_path=destination_path,
            )
            if report is not None:
                processed_changed = self._store_file_field_if_needed(
                    instance=cast(PersistedMigrationModel, report),
                    field_name="processed_file",
                    relative_name=rel_path,
                    source_path=source_path,
                    payload_kind=PayloadKind.REPORT_PDF,
                )
                if processed_changed:
                    update_fields: list[str] = ["processed_file"]
                    cast("PersistedMigrationModel", report).save(
                        update_fields=update_fields,
                    )
                    updated_count = 1
        elif rule.target_root == SENSITIVE_REPORT_DIR:
            report = self._resolve_unique_report(
                content_hash=content_hash,
                stem_hash=stem_hash,
                source_path=source_path,
                destination_path=destination_path,
            )
            if report is not None:
                changed = self._store_file_field_if_needed(
                    instance=cast(PersistedMigrationModel, report),
                    field_name="file",
                    relative_name=rel_path,
                    source_path=source_path,
                    payload_kind=PayloadKind.REPORT_PDF,
                )
                if changed:
                    cast("PersistedMigrationModel", report).save(update_fields=["file"])
                    updated_count = 1

        if updated_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Synced {updated_count} DB record(s) for: {source_path.name}"
                )
            )
            return True
        return False

    @staticmethod
    def _raw_streamable_disabled(rule: MigrationRule) -> bool:
        return rule.target_root == _streamable_raw_video_root() and (
            requires_app_encrypted_storage(PayloadKind.VIDEO_RAW)
        )

    @staticmethod
    def _current_field_value(
        instance: PersistedMigrationModel, field_name: str
    ) -> MigrationFieldValue:
        value = getattr(instance, field_name)
        if hasattr(value, "name"):
            return cast(MigrationFieldValue, cast(_MigrationFieldFile, value).name)
        return cast(MigrationFieldValue, value)

    @staticmethod
    def _field_file_is_valid_encrypted(
        field_file: _MigrationFieldFile, relative_name: str
    ) -> bool:
        storage = field_file.storage
        if not field_file.name:
            return False
        try:
            if not storage.exists(relative_name):
                return False
            if hasattr(storage, "is_encrypted"):
                return cast(_MigrationEncryptedStorage, storage).is_encrypted(
                    relative_name
                )
            return True
        except Exception as exc:
            logger.warning(
                "Could not validate encrypted storage artifact %s: %s",
                relative_name,
                exc,
            )
            return False

    @classmethod
    def _store_file_field_if_needed(
        cls,
        *,
        instance: PersistedMigrationModel,
        field_name: str,
        relative_name: str,
        source_path: Path,
        payload_kind: PayloadKind,
        force_upload_to: bool = False,
    ) -> bool:
        field_file = cast(_MigrationFieldFile, getattr(instance, field_name))
        save_name, storage_name = cls._field_storage_names(
            instance=instance,
            field_file=field_file,
            relative_name=relative_name,
            force_upload_to=force_upload_to,
        )
        current_name = field_file.name
        if requires_app_encrypted_storage(payload_kind):
            if current_name == storage_name and cls._field_file_is_valid_encrypted(
                field_file,
                storage_name,
            ):
                return False
            save_local_file(
                cast(FieldFile, field_file),
                source_path,
                name=save_name,
                save=False,
                overwrite=True,
            )
            return True

        if current_name == storage_name:
            return False
        setattr(instance, field_name, storage_name)
        return True

    @staticmethod
    def _field_storage_names(
        *,
        instance: PersistedMigrationModel,
        field_file: _MigrationFieldFile,
        relative_name: str,
        force_upload_to: bool = False,
    ) -> tuple[str, str]:
        requested_name = Path(relative_name).as_posix()
        field = field_file.field
        upload_prefix = Path(field.upload_to).as_posix().strip("/")

        save_name = requested_name
        if upload_prefix:
            prefix = f"{upload_prefix}/"
            if requested_name == upload_prefix:
                save_name = Path(requested_name).name
            elif requested_name.startswith(prefix):
                save_name = requested_name[len(prefix) :]

        if force_upload_to:
            try:
                storage_name = field.generate_filename(instance, save_name)
            except Exception as exc:
                logger.warning(
                    "Could not derive forced storage name for %s from %s: %s",
                    field_file,
                    save_name,
                    exc,
                )
                storage_name = save_name
            normalized_storage_name = Path(storage_name).as_posix()
            return normalized_storage_name, normalized_storage_name

        if "/" in save_name or "\\" in save_name:
            return save_name, save_name

        try:
            storage_name = field.generate_filename(instance, save_name)
        except Exception as exc:
            logger.warning(
                "Could not derive storage name for %s from %s: %s",
                field_file,
                save_name,
                exc,
            )
            storage_name = save_name
        return save_name, Path(storage_name).as_posix()

    @classmethod
    def _changed_fields(
        cls,
        instance: PersistedMigrationModel,
        fields: dict[str, MigrationFieldValue],
    ) -> dict[str, MigrationFieldValue]:
        changed: dict[str, MigrationFieldValue] = {}
        for field_name, desired_value in fields.items():
            current_value = cls._current_field_value(instance, field_name)
            if current_value != desired_value:
                changed[field_name] = desired_value
        return changed

    @classmethod
    def _update_video_if_changed(
        cls, video: VideoFile, **fields: MigrationFieldValue
    ) -> int:
        changed = cls._changed_fields(cast(PersistedMigrationModel, video), fields)
        if not changed:
            return 0
        return VideoFile.objects.filter(pk=video.pk).update(**changed)

    @classmethod
    def _update_report_if_changed(
        cls, report: RawPdfFile, **fields: MigrationFieldValue
    ) -> int:
        changed = cls._changed_fields(cast(PersistedMigrationModel, report), fields)
        if not changed:
            return 0
        return RawPdfFile.objects.filter(pk=report.pk).update(**changed)

    @staticmethod
    def _canonical_hash_stem(path: Path) -> str | NoMigrationValue:
        stem = path.stem.strip()
        return stem or None

    def _resolve_unique_video(
        self,
        *,
        content_hash: str,
        stem_hash: str | NoMigrationValue,
        include_processed_hash: bool,
        source_path: Path,
        destination_path: Path,
    ) -> VideoFile | NoMigrationValue:
        query = Q(video_hash=content_hash)
        if include_processed_hash:
            query |= Q(processed_video_hash=content_hash)
        if stem_hash:
            query |= Q(video_hash=stem_hash)
            if include_processed_hash:
                query |= Q(processed_video_hash=stem_hash)

        candidate_ids = list(
            VideoFile.objects.filter(query).values_list("pk", flat=True).distinct()
        )
        if not candidate_ids:
            return None
        if len(candidate_ids) > 1:
            logger.error(
                "Ambiguous video migration sync for source=%s destination=%s "
                "content_hash=%s stem_hash=%s candidate_ids=%s",
                source_path,
                destination_path,
                content_hash,
                stem_hash,
                candidate_ids,
            )
            return None
        return VideoFile.objects.get(pk=candidate_ids[0])

    def _resolve_unique_report(
        self,
        *,
        content_hash: str,
        stem_hash: str | NoMigrationValue,
        source_path: Path,
        destination_path: Path,
    ) -> RawPdfFile | NoMigrationValue:
        query = Q(pdf_hash=content_hash)
        if stem_hash:
            query |= Q(pdf_hash=stem_hash)

        candidate_ids = list(
            RawPdfFile.objects.filter(query).values_list("pk", flat=True).distinct()
        )
        if not candidate_ids:
            return None
        if len(candidate_ids) > 1:
            logger.error(
                "Ambiguous report migration sync for source=%s destination=%s "
                "content_hash=%s stem_hash=%s candidate_ids=%s",
                source_path,
                destination_path,
                content_hash,
                stem_hash,
                candidate_ids,
            )
            return None
        return RawPdfFile.objects.get(pk=candidate_ids[0])

    def _create_or_reuse_migration_upload_job(
        self,
        *,
        migrated_file: Path,
        source_path: Path,
        rule: MigrationRule,
    ) -> UploadJob:
        content_hash = sha256_file(migrated_file)
        existing = UploadJob.objects.filter(
            idempotency_key=f"migration:{content_hash}",
            storage_tier=rule.storage_tier,
        ).first()
        if existing is not None:
            return existing

        relative_path = build_upload_job_relative_path(
            tier=rule.storage_tier,
            filename=migrated_file.name,
            key=content_hash,
        )
        with migrated_file.open("rb") as handle:
            django_file = File(handle, name=relative_path)
            return UploadJob.objects.create(
                file=django_file,
                content_type="",
                status=UploadJob.Status.PENDING,
                source_system="migration",
                idempotency_key=f"migration:{content_hash}",
                ingest_mode=UploadJob.IngestMode.WATCHER,
                original_filename=source_path.name,
                processing_provenance={
                    "entrypoint": "migration",
                    "legacy_source_path": str(source_path),
                    "migrated_destination_path": str(migrated_file),
                    "content_hash": content_hash,
                },
                storage_class=rule.storage_class,
                storage_tier=rule.storage_tier,
                retention_policy=rule.retention_policy,
                source_file_persisted=rule.source_file_persisted,
                cleanup_status=rule.cleanup_status,
            )

    def _resolve_manifest_path(self, *, raw_path: str, source_root: Path) -> Path:
        if raw_path:
            return ensure_within_data_root(Path(raw_path).expanduser().resolve())
        return build_manifest_path(
            command_name="migrate_data_dir",
            stem=f"{source_root.name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        )
