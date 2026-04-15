from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from endoreg_db.management.commands.migrate_data_dir import Command, MIGRATION_RULES
from endoreg_db.models import Center, RawPdfFile, VideoFile
from endoreg_db.utils.paths import (
    DOCUMENT_DIR,
    FRAME_DIR,
    IMPORT_ANONYMIZED_REPORT_DIR,
    IMPORT_ANONYMIZED_VIDEO_DIR,
    INGEST_UPLOADS_DIR,
    MANIFEST_DIR,
    MANAGED_SENSITIVE_SIDECARS_DIR,
    RAW_FRAME_DIR,
    WEIGHTS_DIR,
    to_storage_relative,
)


class MigrateDataDirCommandTests(TestCase):
    def test_dry_run_writes_manifest_without_copying_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"
            (legacy_root / "import" / "report_import").mkdir(parents=True)
            (legacy_root / "import" / "report_import" / "report.pdf").write_bytes(
                b"%PDF-1.4\n%%EOF\n"
            )

            manifest_path = MANIFEST_DIR / "tests" / "migrate_data_dir_dry_run.json"
            if manifest_path.exists():
                manifest_path.unlink()

            call_command(
                "migrate_data_dir",
                str(legacy_root),
                "--dry-run",
                "--manifest-path",
                str(manifest_path),
            )

            self.assertTrue(manifest_path.exists())
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["dry_run"])
            self.assertEqual(len(payload["migrated_entries"]), 1)
            self.assertEqual(payload["migrated_entries"][0]["storage_class"], "ingest")

    def test_dry_run_includes_sensitive_raw_storage_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"
            (legacy_root / "sensitive_videos").mkdir(parents=True)
            (legacy_root / "sensitive_reports").mkdir(parents=True)
            (legacy_root / "storage" / "sensitive_videos").mkdir(parents=True)
            (legacy_root / "storage" / "sensitive_reports").mkdir(parents=True)
            (legacy_root / "sensitive_videos" / "raw-root.mp4").write_bytes(
                b"\x00\x00\x00\x18ftypmp42"
            )
            (legacy_root / "sensitive_reports" / "raw-root.pdf").write_bytes(
                b"%PDF-1.4\n%%EOF\n"
            )
            (legacy_root / "storage" / "sensitive_videos" / "raw.mp4").write_bytes(
                b"\x00\x00\x00\x18ftypmp42"
            )
            (legacy_root / "storage" / "sensitive_reports" / "raw.pdf").write_bytes(
                b"%PDF-1.4\n%%EOF\n"
            )

            manifest_path = (
                MANIFEST_DIR / "tests" / "migrate_data_dir_sensitive_raw_dry_run.json"
            )
            if manifest_path.exists():
                manifest_path.unlink()

            call_command(
                "migrate_data_dir",
                str(legacy_root),
                "--dry-run",
                "--manifest-path",
                str(manifest_path),
            )

            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            migrated = payload["migrated_entries"]
            self.assertEqual(len(migrated), 4)
            self.assertTrue(
                all(entry["storage_class"] == "managed" for entry in migrated)
            )
            destinations = {entry["destination_path"] for entry in migrated}
            self.assertTrue(any("/sensitive_videos/" in path for path in destinations))
            self.assertTrue(any("/sensitive_reports/" in path for path in destinations))

    def test_dry_run_preserves_anonymized_import_tier_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"
            (legacy_root / "import" / "anonymized_video_import").mkdir(parents=True)
            (legacy_root / "import" / "anonymized_report_import").mkdir(parents=True)
            (
                legacy_root / "import" / "anonymized_video_import" / "video.mp4"
            ).write_bytes(b"\x00\x00\x00\x18ftypmp42")
            (
                legacy_root / "import" / "anonymized_report_import" / "report.pdf"
            ).write_bytes(b"%PDF-1.4\n%%EOF\n")

            manifest_path = (
                MANIFEST_DIR
                / "tests"
                / "migrate_data_dir_anonymized_import_dry_run.json"
            )
            if manifest_path.exists():
                manifest_path.unlink()

            call_command(
                "migrate_data_dir",
                str(legacy_root),
                "--dry-run",
                "--manifest-path",
                str(manifest_path),
            )

            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            migrated = payload["migrated_entries"]
            self.assertEqual(len(migrated), 2)

            by_legacy = {entry["source_path"]: entry for entry in migrated}
            video_entry = by_legacy[
                str(legacy_root / "import" / "anonymized_video_import" / "video.mp4")
            ]
            report_entry = by_legacy[
                str(legacy_root / "import" / "anonymized_report_import" / "report.pdf")
            ]

            self.assertEqual(video_entry["storage_class"], "ingest")
            self.assertEqual(report_entry["storage_class"], "ingest")
            self.assertTrue(
                video_entry["destination_path"].startswith(
                    str(IMPORT_ANONYMIZED_VIDEO_DIR)
                )
            )
            self.assertTrue(
                report_entry["destination_path"].startswith(
                    str(IMPORT_ANONYMIZED_REPORT_DIR)
                )
            )

    def test_dry_run_includes_remaining_managed_storage_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"

            (legacy_root / "raw_frames").mkdir(parents=True)
            (legacy_root / "frames").mkdir(parents=True)
            (legacy_root / "model_weights").mkdir(parents=True)
            (legacy_root / "documents").mkdir(parents=True)
            (legacy_root / "upload_jobs" / "api" / "aa" / "job1").mkdir(parents=True)
            (legacy_root / "sensitive_sidecars").mkdir(parents=True)
            (legacy_root / "storage" / "raw_frames").mkdir(parents=True)
            (legacy_root / "storage" / "frames").mkdir(parents=True)
            (legacy_root / "storage" / "model_weights").mkdir(parents=True)
            (legacy_root / "storage" / "documents").mkdir(parents=True)
            (legacy_root / "storage" / "upload_jobs" / "watcher" / "bb" / "job2").mkdir(
                parents=True
            )
            (legacy_root / "storage" / "sensitive_sidecars").mkdir(parents=True)

            (legacy_root / "raw_frames" / "frame_a.jpg").write_bytes(b"jpg")
            (legacy_root / "frames" / "frame_b.jpg").write_bytes(b"jpg")
            (legacy_root / "model_weights" / "weights.safetensors").write_bytes(
                b"weights"
            )
            (legacy_root / "documents" / "doc_a.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
            (
                legacy_root / "upload_jobs" / "api" / "aa" / "job1" / "upload_a.pdf"
            ).write_bytes(b"%PDF-1.4\n%%EOF\n")
            (legacy_root / "sensitive_sidecars" / "meta_a.json").write_text(
                "{}",
                encoding="utf-8",
            )
            (legacy_root / "storage" / "raw_frames" / "frame_c.jpg").write_bytes(b"jpg")
            (legacy_root / "storage" / "frames" / "frame_d.jpg").write_bytes(b"jpg")
            (
                legacy_root / "storage" / "model_weights" / "weights2.safetensors"
            ).write_bytes(b"weights")
            (legacy_root / "storage" / "documents" / "doc_b.pdf").write_bytes(
                b"%PDF-1.4\n%%EOF\n"
            )
            (
                legacy_root
                / "storage"
                / "upload_jobs"
                / "watcher"
                / "bb"
                / "job2"
                / "upload_b.mp4"
            ).write_bytes(b"\x00\x00\x00\x18ftypmp42")
            (legacy_root / "storage" / "sensitive_sidecars" / "meta_b.json").write_text(
                "{}", encoding="utf-8"
            )

            manifest_path = MANIFEST_DIR / "tests" / "migrate_data_dir_managed.json"
            if manifest_path.exists():
                manifest_path.unlink()

            call_command(
                "migrate_data_dir",
                str(legacy_root),
                "--dry-run",
                "--manifest-path",
                str(manifest_path),
            )

            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            migrated = payload["migrated_entries"]
            self.assertEqual(len(migrated), 12)

            destination_paths = {entry["destination_path"] for entry in migrated}
            self.assertTrue(
                any(path.startswith(str(RAW_FRAME_DIR)) for path in destination_paths)
            )
            self.assertTrue(
                any(path.startswith(str(FRAME_DIR)) for path in destination_paths)
            )
            self.assertTrue(
                any(path.startswith(str(WEIGHTS_DIR)) for path in destination_paths)
            )
            self.assertTrue(
                any(path.startswith(str(DOCUMENT_DIR)) for path in destination_paths)
            )
            self.assertTrue(
                any(
                    path.startswith(str(INGEST_UPLOADS_DIR))
                    for path in destination_paths
                )
            )
            self.assertTrue(
                any(
                    path.startswith(str(MANAGED_SENSITIVE_SIDECARS_DIR))
                    for path in destination_paths
                )
            )

    def test_sync_db_links_anonymized_import_assets_by_canonical_filename_stem(
        self,
    ) -> None:
        center = Center.objects.create(name="migration-sync-center")
        video = VideoFile.objects.create(
            center=center,
            video_hash="video-sync-hash",
            original_file_name="legacy.mp4",
        )
        report = RawPdfFile.objects.create(pdf_hash="report-sync-hash")

        video_rule = next(
            rule
            for rule in MIGRATION_RULES
            if rule.legacy_relative == Path("import/anonymized_video_import")
        )
        report_rule = next(
            rule
            for rule in MIGRATION_RULES
            if rule.legacy_relative == Path("import/anonymized_report_import")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            video_source = (
                temp_dir
                / "legacy"
                / "import"
                / "anonymized_video_import"
                / "video-sync-hash.mp4"
            )
            report_source = (
                temp_dir
                / "legacy"
                / "import"
                / "anonymized_report_import"
                / "report-sync-hash.pdf"
            )
            video_source.parent.mkdir(parents=True, exist_ok=True)
            report_source.parent.mkdir(parents=True, exist_ok=True)
            video_source.write_bytes(b"\x00\x00\x00\x18ftypmp42processed")
            report_source.write_bytes(b"%PDF-1.4\nprocessed\n%%EOF\n")

            video_destination = IMPORT_ANONYMIZED_VIDEO_DIR / video_source.name
            report_destination = IMPORT_ANONYMIZED_REPORT_DIR / report_source.name

            try:
                video_destination.parent.mkdir(parents=True, exist_ok=True)
                report_destination.parent.mkdir(parents=True, exist_ok=True)
                video_destination.write_bytes(video_source.read_bytes())
                report_destination.write_bytes(report_source.read_bytes())

                command = Command()

                self.assertTrue(
                    command.sync_db(video_destination, video_source, video_rule)
                )
                self.assertTrue(
                    command.sync_db(report_destination, report_source, report_rule)
                )
                self.assertTrue(
                    command.sync_db(video_destination, video_source, video_rule)
                )
                self.assertTrue(
                    command.sync_db(report_destination, report_source, report_rule)
                )

                video.refresh_from_db()
                report.refresh_from_db()

                self.assertEqual(
                    video.processed_file.name,
                    to_storage_relative(video_destination),
                )
                self.assertEqual(
                    report.processed_file.name,
                    to_storage_relative(report_destination),
                )
                self.assertEqual(
                    report.file.name,
                    to_storage_relative(report_destination),
                )
            finally:
                video_destination.unlink(missing_ok=True)
                report_destination.unlink(missing_ok=True)
