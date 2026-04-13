from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from endoreg_db.utils.paths import MANIFEST_DIR


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
