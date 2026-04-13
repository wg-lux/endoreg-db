from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import logging
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from endoreg_db.models import UploadJob, VideoFile, RawPdfFile
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.utils.file_operations import atomic_copy_file
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import (
    MANAGED_ANONYMIZED_REPORTS_DIR,
    MANAGED_ANONYMIZED_VIDEOS_DIR,
    SAP_IMPORT_DROP_DIR,
    SAP_IMPORT_FAILED_DIR,
    SAP_IMPORT_PROCESSED_DIR,
    SENSITIVE_REPORT_DIR,
    SENSITIVE_VIDEO_DIR,
    WATCHER_PREANONYMIZED_DROP_DIR,
    WATCHER_REPORT_DROP_DIR,
    WATCHER_VIDEO_DROP_DIR,
    to_storage_relative,
    build_manifest_path,
    build_upload_job_relative_path,
    ensure_within_protected_root,
)

logger = logging.getLogger(__name__)


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


MIGRATION_RULES: tuple[MigrationRule, ...] = (
    MigrationRule(
        legacy_relative=Path("import/video_import"),
        target_root=WATCHER_VIDEO_DROP_DIR,
        create_upload_job=True,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
    ),
    MigrationRule(
        legacy_relative=Path("import/report_import"),
        target_root=WATCHER_REPORT_DROP_DIR,
        create_upload_job=True,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
    ),
    MigrationRule(
        legacy_relative=Path("import/preanonymized_import"),
        target_root=WATCHER_PREANONYMIZED_DROP_DIR,
        create_upload_job=True,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
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
    ),
)


class Command(BaseCommand):
    help = (
        "Migrate a legacy data directory into the protected storage tiers under "
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR and emit a JSON manifest."
    )

    def add_arguments(self, parser) -> None:
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
            help="Optional manifest path inside the protected manifest tier.",
        )

    def handle(self, *args, **options) -> None:
        source_root = Path(options["source_root"]).expanduser().resolve()
        if not source_root.exists() or not source_root.is_dir():
            raise CommandError(f"Legacy source root does not exist: {source_root}")

        dry_run = bool(options["dry_run"])
        manifest_path = self._resolve_manifest_path(
            raw_path=str(options.get("manifest_path") or "").strip(),
            source_root=source_root,
        )

        migrated_entries: list[dict[str, object]] = []
        skipped_entries: list[dict[str, object]] = []

        for rule in MIGRATION_RULES:
            legacy_dir = source_root / rule.legacy_relative
            if not legacy_dir.exists():
                continue
            for source_path in sorted(legacy_dir.rglob("*")):
                if not source_path.is_file():
                    continue
                destination_path = ensure_within_protected_root(
                    rule.target_root / source_path.relative_to(legacy_dir)
                )
                entry = {
                    "source_path": str(source_path),
                    "destination_path": str(destination_path),
                    "storage_class": rule.storage_class,
                    "storage_tier": rule.storage_tier,
                    "retention_policy": rule.retention_policy,
                    "create_upload_job": rule.create_upload_job,
                }
                # FIX 1: Ignore lock files
                if source_path.suffix.lower() == ".lock":
                    logger.warning(f"Skipping lock file: {source_path}")
                    continue

                # FIX 2: Ignore non-media junk (Optional, but highly recommended)
                allowed_extensions = {".mp4", ".webm", ".avi", ".mkv", ".pdf", ".txt"}
                if source_path.suffix.lower() not in allowed_extensions:
                    logger.warning(f"Skipping unsupported file type: {source_path}")
                    continue

                destination_path = ensure_within_protected_root(
                    rule.target_root / source_path.relative_to(legacy_dir)
                )

                # 2. If it already exists at the destination, run the "Database Sync" logic
                if destination_path.exists():
                    self.sync_db(destination_path, source_path, rule)
                    skipped_entries.append(
                        {**entry, "reason": "destination_exists_and_synced"}
                    )
                    continue

                if dry_run:
                    migrated_entries.append({**entry, "dry_run": True})
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
                    job_id = str(job.id)
                if self.sync_db(destination_path, source_path, rule):
                    logger.info(
                        f"synced db path of {destination_path} to {source_path}"
                    )
                else:
                    logger.info(
                        f"No existing DB records found for {source_path.name} to sync."
                    )
                migrated_entries.append({**entry, "upload_job_id": job_id})

        manifest = {
            "command": "migrate_data_dir",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(source_root),
            "dry_run": dry_run,
            "migrated_entries": migrated_entries,
            "skipped_entries": skipped_entries,
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
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

        # Calculate hash to find the record in the DB
        content_hash = sha256_file(destination_path)
        rel_path = to_storage_relative(destination_path)
        updated_count = 0
        # Re-point existing Video records
        if rule.target_root == MANAGED_ANONYMIZED_VIDEOS_DIR:
            updated_count = VideoFile.objects.filter(
                Q(processed_video_hash=content_hash) | Q(video_hash=content_hash)
            ).update(
                processed_file=rel_path,
            )
        elif rule.target_root == SENSITIVE_VIDEO_DIR:
            updated_count = VideoFile.objects.filter(video_hash=content_hash).update(
                raw_file=rel_path
            )
        elif rule.target_root == _streamable_raw_video_root():
            updated_count = VideoFile.objects.filter(video_hash=content_hash).update(
                streamable_relative_path=rel_path,
                storage_mode=VideoStorageMode.STREAMABLE,
            )
        elif rule.target_root == _streamable_processed_video_root():
            updated_count = VideoFile.objects.filter(
                Q(processed_video_hash=content_hash) | Q(video_hash=content_hash)
            ).update(
                processed_streamable_relative_path=rel_path,
                storage_mode=VideoStorageMode.STREAMABLE,
            )

        # Re-point existing Report records
        elif rule.target_root == MANAGED_ANONYMIZED_REPORTS_DIR:
            updated_count = RawPdfFile.objects.filter(pdf_hash=content_hash).update(
                processed_file=rel_path
            )
        elif rule.target_root == SENSITIVE_REPORT_DIR:
            updated_count = RawPdfFile.objects.filter(pdf_hash=content_hash).update(
                file=rel_path
            )

        if updated_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Synced {updated_count} DB record(s) for: {source_path.name}"
                )
            )
            return True
        return False

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
            return ensure_within_protected_root(Path(raw_path).expanduser().resolve())
        return build_manifest_path(
            command_name="migrate_data_dir",
            stem=f"{source_root.name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        )
