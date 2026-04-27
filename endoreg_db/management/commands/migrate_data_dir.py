from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from endoreg_db.models import RawPdfFile, UploadJob, VideoFile
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.utils.file_operations import atomic_copy_file, sha256_file
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

logger = logging.getLogger(__name__)


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
    allowed_extensions: tuple[str, ...] | None = None


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
                destination_path = _ensure_within_runtime_root(
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
                allowed_extensions = rule.allowed_extensions
                if (
                    allowed_extensions is not None
                    and source_path.suffix.lower() not in allowed_extensions
                ):
                    logger.warning(f"Skipping unsupported file type: {source_path}")
                    continue

                destination_path = _ensure_within_runtime_root(
                    rule.target_root / source_path.relative_to(legacy_dir)
                )

                if dry_run:
                    migrated_entries.append(
                        {
                            **entry,
                            "dry_run": True,
                            "destination_exists": destination_path.exists(),
                        }
                    )
                    continue

                # 2. If it already exists at the destination, run the "Database Sync" logic
                if destination_path.exists():
                    self.sync_db(destination_path, source_path, rule)
                    skipped_entries.append(
                        {**entry, "reason": "destination_exists_and_synced"}
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

        # Calculate content hash and derive the canonical hash stem from the filename.
        # Processed media is typically named after the raw object hash, not the
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
                video_update_fields: dict[str, object] = {"processed_file": rel_path}
                processed_video_hash = getattr(video, "processed_video_hash", None)
                if not processed_video_hash:
                    video_update_fields["processed_video_hash"] = content_hash
                elif processed_video_hash != content_hash:
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
                updated_count = VideoFile.objects.filter(pk=video.pk).update(
                    **video_update_fields
                )
        elif rule.target_root == SENSITIVE_VIDEO_DIR:
            video = self._resolve_unique_video(
                content_hash=content_hash,
                stem_hash=stem_hash,
                include_processed_hash=False,
                source_path=source_path,
                destination_path=destination_path,
            )
            if video is not None:
                updated_count = VideoFile.objects.filter(pk=video.pk).update(
                    raw_file=rel_path
                )
        elif rule.target_root == _streamable_raw_video_root():
            video = self._resolve_unique_video(
                content_hash=content_hash,
                stem_hash=stem_hash,
                include_processed_hash=False,
                source_path=source_path,
                destination_path=destination_path,
            )
            if video is not None:
                updated_count = VideoFile.objects.filter(pk=video.pk).update(
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
                updated_count = VideoFile.objects.filter(pk=video.pk).update(
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
                report_update_fields: dict[str, object] = {"processed_file": rel_path}
                if not getattr(report.file, "name", None):
                    report_update_fields["file"] = rel_path
                updated_count = RawPdfFile.objects.filter(pk=report.pk).update(
                    **report_update_fields
                )
        elif rule.target_root == SENSITIVE_REPORT_DIR:
            report = self._resolve_unique_report(
                content_hash=content_hash,
                stem_hash=stem_hash,
                source_path=source_path,
                destination_path=destination_path,
            )
            if report is not None:
                updated_count = RawPdfFile.objects.filter(pk=report.pk).update(
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

    @staticmethod
    def _canonical_hash_stem(path: Path) -> str | None:
        stem = path.stem.strip()
        return stem or None

    def _resolve_unique_video(
        self,
        *,
        content_hash: str,
        stem_hash: str | None,
        include_processed_hash: bool,
        source_path: Path,
        destination_path: Path,
    ) -> VideoFile | None:
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
        stem_hash: str | None,
        source_path: Path,
        destination_path: Path,
    ) -> RawPdfFile | None:
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
