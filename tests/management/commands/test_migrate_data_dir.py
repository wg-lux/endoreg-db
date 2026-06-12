from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from endoreg_db.management.commands.migrate_data_dir import Command, MIGRATION_RULES
from endoreg_db.models import Center, RawPdfFile, VideoFile
from endoreg_db.models.media.video.storage_mode import VideoStorageMode
from endoreg_db.utils.paths import (
    DOCUMENT_DIR,
    FRAME_DIR,
    IMPORT_ANONYMIZED_REPORT_DIR,
    IMPORT_ANONYMIZED_VIDEO_DIR,
    MANIFEST_DIR,
    MANAGED_ANONYMIZED_VIDEOS_DIR,
    MANAGED_SENSITIVE_SIDECARS_DIR,
    RAW_FRAME_DIR,
    SENSITIVE_VIDEO_DIR,
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

    def test_dry_run_skips_raw_streamable_video_by_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"
            raw_streamable_dir = legacy_root / "streamable_videos" / "raw"
            raw_streamable_dir.mkdir(parents=True)
            (raw_streamable_dir / "raw.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

            manifest_path = (
                MANIFEST_DIR / "tests" / "migrate_data_dir_raw_streamable.json"
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
            self.assertEqual(payload["migrated_entries"], [])
            self.assertEqual(len(payload["skipped_entries"]), 1)
            self.assertEqual(
                payload["skipped_entries"][0]["reason"],
                "raw_streamable_disabled_by_policy",
            )

    def test_dry_run_includes_remaining_managed_storage_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"

            (legacy_root / "raw_frames").mkdir(parents=True)
            (legacy_root / "frames").mkdir(parents=True)
            (legacy_root / "model_weights").mkdir(parents=True)
            (legacy_root / "documents").mkdir(parents=True)
            (legacy_root / "sensitive_sidecars").mkdir(parents=True)
            (legacy_root / "storage" / "raw_frames").mkdir(parents=True)
            (legacy_root / "storage" / "frames").mkdir(parents=True)
            (legacy_root / "storage" / "model_weights").mkdir(parents=True)
            (legacy_root / "storage" / "documents").mkdir(parents=True)
            (legacy_root / "storage" / "sensitive_sidecars").mkdir(parents=True)

            (legacy_root / "raw_frames" / "frame_a.jpg").write_bytes(b"jpg")
            (legacy_root / "frames" / "frame_b.jpg").write_bytes(b"jpg")
            (legacy_root / "model_weights" / "weights.safetensors").write_bytes(
                b"weights"
            )
            (legacy_root / "documents" / "doc_a.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
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
            self.assertEqual(len(migrated), 10)

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
                    path.startswith(str(MANAGED_SENSITIVE_SIDECARS_DIR))
                    for path in destination_paths
                )
            )

    def test_dry_run_maps_legacy_top_level_media_dirs_to_canonical_storage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"
            legacy_sources = {
                Path("sensitive_videos/raw.mp4"): SENSITIVE_VIDEO_DIR,
                Path("processed_videos_final/processed.mp4"): (
                    MANAGED_ANONYMIZED_VIDEOS_DIR
                ),
                Path("frames/frame.jpg"): FRAME_DIR,
                Path("model_weights/weights.safetensors"): WEIGHTS_DIR,
            }
            for relative_path in legacy_sources:
                source_path = legacy_root / relative_path
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(b"migration-source")

            manifest_path = (
                MANIFEST_DIR / "tests" / "migrate_data_dir_top_level_media_mapping.json"
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
            migrated_by_source = {
                Path(entry["source_path"]).relative_to(legacy_root): entry
                for entry in payload["migrated_entries"]
            }

            self.assertEqual(set(migrated_by_source), set(legacy_sources))
            for relative_path, target_root in legacy_sources.items():
                entry = migrated_by_source[relative_path]
                self.assertEqual(entry["storage_class"], "managed")
                self.assertTrue(entry["destination_path"].startswith(str(target_root)))

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
                self.assertFalse(
                    command.sync_db(video_destination, video_source, video_rule)
                )
                self.assertFalse(
                    command.sync_db(report_destination, report_source, report_rule)
                )

                video.refresh_from_db()
                report.refresh_from_db()

                expected_video_processed_name = to_storage_relative(video_destination)
                expected_report_processed_name = to_storage_relative(report_destination)

                self.assertEqual(
                    video.processed_file.name,
                    expected_video_processed_name,
                )
                self.assertEqual(
                    report.processed_file.name,
                    expected_report_processed_name,
                )
                self.assertFalse(report.file.name)
            finally:
                video_destination.unlink(missing_ok=True)
                report_destination.unlink(missing_ok=True)

    def test_sync_db_skips_ambiguous_video_matches(self) -> None:
        center = Center.objects.create(name="migration-ambiguous-video-center")
        rule = next(
            candidate
            for candidate in MIGRATION_RULES
            if candidate.legacy_relative == Path("import/anonymized_video_import")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_path = (
                temp_dir
                / "legacy"
                / "import"
                / "anonymized_video_import"
                / "stem-ambiguous.mp4"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42ambiguous")
            destination_path = IMPORT_ANONYMIZED_VIDEO_DIR / source_path.name

            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(source_path.read_bytes())

                stem_hash = destination_path.stem

                # Two distinct rows match different parts of the OR query.
                video_by_hash = VideoFile.objects.create(
                    center=center,
                    video_hash=stem_hash,
                    original_file_name="first.mp4",
                )
                video_by_processed_hash = VideoFile.objects.create(
                    center=center,
                    video_hash="distinct-video-hash",
                    processed_video_hash=stem_hash,
                    original_file_name="second.mp4",
                )

                synced = Command().sync_db(destination_path, source_path, rule)
                self.assertFalse(synced)

                video_by_hash.refresh_from_db()
                video_by_processed_hash.refresh_from_db()
                self.assertEqual(video_by_hash.processed_file.name, "")
                self.assertEqual(video_by_processed_hash.processed_file.name, "")
            finally:
                destination_path.unlink(missing_ok=True)

    def test_sync_db_returns_false_when_video_record_is_missing(self) -> None:
        rule = next(
            candidate
            for candidate in MIGRATION_RULES
            if candidate.legacy_relative == Path("import/anonymized_video_import")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_path = (
                temp_dir
                / "legacy"
                / "import"
                / "anonymized_video_import"
                / "missing-video.mp4"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42missing")
            destination_path = IMPORT_ANONYMIZED_VIDEO_DIR / source_path.name

            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(source_path.read_bytes())
                self.assertFalse(Command().sync_db(destination_path, source_path, rule))
            finally:
                destination_path.unlink(missing_ok=True)

    def test_sync_db_keeps_existing_processed_video_hash_on_mismatch(self) -> None:
        center = Center.objects.create(name="migration-hash-mismatch-center")
        rule = next(
            candidate
            for candidate in MIGRATION_RULES
            if candidate.legacy_relative == Path("import/anonymized_video_import")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_path = (
                temp_dir
                / "legacy"
                / "import"
                / "anonymized_video_import"
                / "video-hash-stem.mp4"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42new-content")
            destination_path = IMPORT_ANONYMIZED_VIDEO_DIR / source_path.name

            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(source_path.read_bytes())
                existing_processed_hash = "existing-processed-hash"
                video = VideoFile.objects.create(
                    center=center,
                    video_hash=destination_path.stem,
                    processed_video_hash=existing_processed_hash,
                    original_file_name="legacy.mp4",
                )

                self.assertTrue(Command().sync_db(destination_path, source_path, rule))

                video.refresh_from_db()
                self.assertEqual(video.processed_video_hash, existing_processed_hash)
                expected_processed_name = video.processed_file.field.generate_filename(
                    video,
                    to_storage_relative(destination_path),
                )
                self.assertEqual(
                    video.processed_file.name,
                    expected_processed_name,
                )
            finally:
                destination_path.unlink(missing_ok=True)

    def test_sync_db_updates_streamable_processed_path_and_storage_mode(self) -> None:
        center = Center.objects.create(name="migration-streamable-center")
        rule = next(
            candidate
            for candidate in MIGRATION_RULES
            if candidate.legacy_relative == Path("streamable_videos/processed")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_path = (
                temp_dir
                / "legacy"
                / "streamable_videos"
                / "processed"
                / "streamable-stem.mp4"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42streamable")
            destination_path = rule.target_root / source_path.name

            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(source_path.read_bytes())
                video = VideoFile.objects.create(
                    center=center,
                    video_hash=source_path.stem,
                    original_file_name="streamable.mp4",
                )

                self.assertTrue(Command().sync_db(destination_path, source_path, rule))

                video.refresh_from_db()
                self.assertEqual(
                    video.processed_streamable_relative_path,
                    to_storage_relative(destination_path),
                )
                self.assertEqual(video.storage_mode, VideoStorageMode.STREAMABLE.value)
            finally:
                destination_path.unlink(missing_ok=True)

    def test_sync_db_refuses_raw_streamable_path_when_policy_requires_app_encryption(
        self,
    ) -> None:
        center = Center.objects.create(name="migration-raw-streamable-center")
        rule = next(
            candidate
            for candidate in MIGRATION_RULES
            if candidate.legacy_relative == Path("streamable_videos/raw")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_path = (
                temp_dir
                / "legacy"
                / "streamable_videos"
                / "raw"
                / "raw-streamable-stem.mp4"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42raw-streamable")
            destination_path = rule.target_root / source_path.name

            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(source_path.read_bytes())
                video = VideoFile.objects.create(
                    center=center,
                    video_hash=source_path.stem,
                    original_file_name="raw-streamable.mp4",
                )

                self.assertFalse(Command().sync_db(destination_path, source_path, rule))

                video.refresh_from_db()
                self.assertEqual(video.raw_streamable_relative_path, "")
                self.assertNotEqual(
                    video.storage_mode,
                    VideoStorageMode.STREAMABLE.value,
                )
            finally:
                destination_path.unlink(missing_ok=True)

    def test_migrate_data_dir_does_not_overwrite_existing_processed_video_destination(
        self,
    ) -> None:
        center = Center.objects.create(name="migration-destination-collision-center")
        rule = next(
            candidate
            for candidate in MIGRATION_RULES
            if candidate.legacy_relative == Path("processed_videos_final")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"
            primary_source = (
                legacy_root / "processed_videos_final" / "collision-stem.mp4"
            )
            duplicate_source = (
                legacy_root
                / "storage"
                / "processed_videos_final"
                / "collision-stem.mp4"
            )
            primary_source.parent.mkdir(parents=True, exist_ok=True)
            duplicate_source.parent.mkdir(parents=True, exist_ok=True)
            primary_source.write_bytes(b"\x00\x00\x00\x18ftypmp42primary")
            duplicate_source.write_bytes(b"\x00\x00\x00\x18ftypmp42duplicate")

            destination_path = rule.target_root / primary_source.name
            manifest_path = (
                MANIFEST_DIR / "tests" / "migrate_data_dir_collision_manifest.json"
            )
            if manifest_path.exists():
                manifest_path.unlink()

            try:
                video = VideoFile.objects.create(
                    center=center,
                    video_hash=destination_path.stem,
                    original_file_name="collision.mp4",
                )

                call_command(
                    "migrate_data_dir",
                    str(legacy_root),
                    "--manifest-path",
                    str(manifest_path),
                )

                self.assertTrue(destination_path.exists())
                self.assertEqual(
                    destination_path.read_bytes(), primary_source.read_bytes()
                )
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                reasons = {entry["reason"] for entry in payload["skipped_entries"]}
                self.assertIn("destination_exists_hash_mismatch", reasons)

                video.refresh_from_db()
                self.assertEqual(
                    video.processed_file.name,
                    to_storage_relative(destination_path),
                )
            finally:
                destination_path.unlink(missing_ok=True)

    def test_migrate_data_dir_second_run_is_idempotent_for_valid_destination(
        self,
    ) -> None:
        center = Center.objects.create(name="migration-idempotent-center")
        rule = next(
            candidate
            for candidate in MIGRATION_RULES
            if candidate.legacy_relative == Path("processed_videos_final")
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            legacy_root = temp_dir / "legacy"
            source_path = legacy_root / "processed_videos_final" / "repeat-stem.mp4"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"\x00\x00\x00\x18ftypmp42repeat")

            destination_path = rule.target_root / source_path.name
            manifest_one = (
                MANIFEST_DIR / "tests" / "migrate_data_dir_idempotent_one.json"
            )
            manifest_two = (
                MANIFEST_DIR / "tests" / "migrate_data_dir_idempotent_two.json"
            )
            for manifest_path in (manifest_one, manifest_two):
                if manifest_path.exists():
                    manifest_path.unlink()

            try:
                video = VideoFile.objects.create(
                    center=center,
                    video_hash=destination_path.stem,
                    original_file_name="repeat.mp4",
                )

                call_command(
                    "migrate_data_dir",
                    str(legacy_root),
                    "--manifest-path",
                    str(manifest_one),
                )
                first_mtime_ns = destination_path.stat().st_mtime_ns

                call_command(
                    "migrate_data_dir",
                    str(legacy_root),
                    "--manifest-path",
                    str(manifest_two),
                )

                second_mtime_ns = destination_path.stat().st_mtime_ns
                second_payload = json.loads(manifest_two.read_text(encoding="utf-8"))
                self.assertEqual(second_payload["migrated_entries"], [])
                self.assertEqual(len(second_payload["skipped_entries"]), 1)
                self.assertEqual(
                    second_payload["skipped_entries"][0]["reason"],
                    "destination_exists_unchanged",
                )
                self.assertEqual(first_mtime_ns, second_mtime_ns)

                video.refresh_from_db()
                self.assertEqual(
                    video.processed_file.name,
                    to_storage_relative(destination_path),
                )
            finally:
                destination_path.unlink(missing_ok=True)
