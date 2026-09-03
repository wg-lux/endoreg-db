from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from endoreg_db.management.commands import import_sap_ish_zip as command_module
from endoreg_db.models import Center
from endoreg_db.utils.paths import WATCHER_PREANONYMIZED_DROP_DIR


def _write_tsv(path: Path, *, header: list[str], rows: list[list[str]]) -> None:
    rendered_rows = ["\t".join(header)]
    rendered_rows.extend("\t".join(row) for row in rows)
    path.write_text("\n".join(rendered_rows) + "\n", encoding="utf-8")


def _build_zip_from_directory(source_dir: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w") as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, arcname=file_path.relative_to(source_dir))


class ImportSapIshZipCommandTests(TestCase):
    def test_command_rejects_missing_zip_archive(self) -> None:
        missing_archive = WATCHER_PREANONYMIZED_DROP_DIR / "missing-sap-export.zip"

        with self.assertRaisesMessage(
            CommandError,
            f"Zip archive does not exist: {missing_archive}",
        ):
            call_command("import_sap_ish_zip", str(missing_archive))

    def test_command_reports_declared_center_resolution_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            archive_path = temp_dir / "sap_export.zip"
            with zipfile.ZipFile(archive_path, "w"):
                pass

            with patch.object(
                command_module,
                "_resolve_declared_upload_center",
                return_value=(None, "Unknown center_key: missing-center"),
            ):
                with self.assertRaisesMessage(CommandError, "Unknown center_key"):
                    call_command(
                        "import_sap_ish_zip",
                        str(archive_path),
                        center_key="missing-center",
                    )

    def test_command_processes_generated_files_when_process_flag_is_enabled(
        self,
    ) -> None:
        center = Center.objects.create(
            name="center-a",
            display_name="Center A",
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_dir = temp_dir / "source"
            source_dir.mkdir()
            archive_path = temp_dir / "sap_export.zip"
            output_dir = WATCHER_PREANONYMIZED_DROP_DIR / f"cmd-test-{temp_dir.name}"
            output_dir.mkdir(parents=True, exist_ok=True)

            _write_tsv(
                source_dir / "briefe.txt",
                header=["PatientNr", "FallNr", "dateErstellzeit", "strText"],
                rows=[
                    [
                        "2001",
                        "3001",
                        "2024-05-17 09:30:00",
                        "Already anonymized letter",
                    ]
                ],
            )
            _build_zip_from_directory(source_dir, archive_path)

            with (
                patch.object(
                    command_module,
                    "_resolve_declared_upload_center",
                    return_value=(center, None),
                ),
                patch.object(
                    command_module,
                    "_process_preanonymized_watcher_file",
                ) as mocked_process,
            ):
                output = io.StringIO()
                call_command(
                    "import_sap_ish_zip",
                    str(archive_path),
                    output_dir=str(output_dir),
                    center_key=center.center_key,
                    source_system="sap_ish_test",
                    process=True,
                    stdout=output,
                )
                self.assertEqual(mocked_process.call_count, 1)
                processed_path = mocked_process.call_args.kwargs["file_path"]
                self.assertTrue(Path(processed_path).exists())
                self.assertEqual(mocked_process.call_args.kwargs["center"], center)
                self.assertEqual(
                    mocked_process.call_args.kwargs["source_system"],
                    "sap_ish_test",
                )
                self.assertIn("Processed 1 generated file(s)", output.getvalue())

    def test_command_uses_default_center_for_immediate_processing(self) -> None:
        center = Center.objects.create(
            name="default-center",
            display_name="Default Center",
        )

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_dir = temp_dir / "source"
            source_dir.mkdir()
            archive_path = temp_dir / "sap_export.zip"
            output_dir = WATCHER_PREANONYMIZED_DROP_DIR / f"cmd-test-{temp_dir.name}"
            output_dir.mkdir(parents=True, exist_ok=True)
            _write_tsv(
                source_dir / "briefe.txt",
                header=["PatientNr", "FallNr", "dateErstellzeit", "strText"],
                rows=[["2001", "3001", "2024-05-17 09:30:00", "Letter"]],
            )
            _build_zip_from_directory(source_dir, archive_path)

            with (
                patch.object(
                    command_module,
                    "_resolve_default_center",
                    return_value=center,
                ) as mocked_resolve_default,
                patch.object(
                    command_module,
                    "_process_preanonymized_watcher_file",
                ) as mocked_process,
            ):
                call_command(
                    "import_sap_ish_zip",
                    str(archive_path),
                    output_dir=str(output_dir),
                    process=True,
                )

            mocked_resolve_default.assert_called_once_with()
            self.assertEqual(mocked_process.call_count, 1)
            self.assertEqual(mocked_process.call_args.kwargs["center"], center)

    def test_command_skips_unsupported_files_and_reports_generated_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            source_dir = temp_dir / "source"
            source_dir.mkdir()
            archive_path = temp_dir / "sap_export.zip"
            output_dir = WATCHER_PREANONYMIZED_DROP_DIR / f"cmd-test-{temp_dir.name}"
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "custom-manifest.json"

            _write_tsv(
                source_dir / "briefe.txt",
                header=["PatientNr", "FallNr", "dateErstellzeit", "strText"],
                rows=[
                    [
                        "2001",
                        "3001",
                        "2024-05-17 09:30:00",
                        "Already anonymized letter",
                    ]
                ],
            )
            _write_tsv(
                source_dir / "unsupported.txt",
                header=["UnknownColumn", "AnotherColumn"],
                rows=[["value-a", "value-b"]],
            )
            _build_zip_from_directory(source_dir, archive_path)

            output = io.StringIO()
            call_command(
                "import_sap_ish_zip",
                str(archive_path),
                output_dir=str(output_dir),
                source_system="sap_ish_test",
                manifest_path=str(manifest_path),
                stdout=output,
            )

            generated_carriers = sorted(output_dir.glob("*.txt"))
            self.assertEqual(len(generated_carriers), 1)
            generated_carrier = generated_carriers[0]
            generated_sidecar = generated_carrier.with_suffix(".json")
            self.assertTrue(generated_carrier.exists())
            self.assertTrue(generated_sidecar.exists())
            payload = json.loads(generated_sidecar.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_system"], "sap_ish_test")
            self.assertIn("Generated 1 watcher file pair(s)", output.getvalue())
            self.assertIn(
                "Skipped unsupported table files: unsupported.txt",
                output.getvalue(),
            )
            self.assertIn(f"Manifest written to {manifest_path}", output.getvalue())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                set(manifest),
                {
                    "command",
                    "created_at",
                    "zip_path",
                    "output_dir",
                    "source_system",
                    "center_name",
                    "center_key",
                    "process",
                    "generated_files",
                    "matched_source_files",
                    "skipped_source_files",
                },
            )
            self.assertEqual(manifest["command"], "import_sap_ish_zip")
            self.assertEqual(manifest["zip_path"], str(archive_path.resolve()))
            self.assertEqual(manifest["output_dir"], str(output_dir.resolve()))
            self.assertEqual(manifest["source_system"], "sap_ish_test")
            self.assertFalse(manifest["process"])
            self.assertEqual(len(manifest["generated_files"]), 1)
            self.assertEqual(
                manifest["generated_files"][0]["carrier_path"],
                str(generated_carrier),
            )
