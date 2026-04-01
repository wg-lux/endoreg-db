from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from endoreg_db.models import UploadJob
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import (
    MANAGED_ANONYMIZED_REPORTS_DIR,
    MANAGED_ANONYMIZED_VIDEOS_DIR,
    SAP_IMPORT_DROP_DIR,
    SAP_IMPORT_FAILED_DIR,
    SAP_IMPORT_PROCESSED_DIR,
    WATCHER_PREANONYMIZED_DROP_DIR,
    WATCHER_REPORT_DROP_DIR,
    WATCHER_VIDEO_DROP_DIR,
    build_manifest_path,
    build_upload_job_relative_path,
    ensure_within_protected_root,
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

                if destination_path.exists():
                    skipped_entries.append({**entry, "reason": "destination_exists"})
                    continue

                if dry_run:
                    migrated_entries.append({**entry, "dry_run": True})
                    continue

                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)
                job_id = None
                if rule.create_upload_job:
                    job = self._create_or_reuse_migration_upload_job(
                        migrated_file=destination_path,
                        source_path=source_path,
                        rule=rule,
                    )
                    job_id = str(job.id)
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
