from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from endoreg_db.models import (
    Case,
    Center,
    Gender,
    PatientDisease,
    PatientExternalID,
    PatientLabValue,
    PatientMedication,
    PatientMedicationSchedule,
    RawPdfFile,
)
from endoreg_db.utils.paths import WATCHER_PREANONYMIZED_DROP_DIR


def _write_tsv(path: Path, *, header: list[str], rows: list[list[str]]) -> None:
    rendered_rows = ["\t".join(header)]
    rendered_rows.extend("\t".join(row) for row in rows)
    path.write_text("\n".join(rendered_rows) + "\n", encoding="utf-8")


class ImportSapIshTxtCommandTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name="sap-txt-center",
            display_name="SAP TXT Center",
        )
        Gender.objects.create(name="female", abbreviation="f")

    def test_command_rejects_missing_source_directory(self) -> None:
        missing_source = Path("/tmp/missing-sap-ish-txt-source")

        with self.assertRaisesMessage(
            CommandError,
            f"Source directory does not exist: {missing_source}",
        ):
            call_command("import_sap_ish_txt", str(missing_source))

    def test_command_writes_yaml_and_persists_through_host_models(self) -> None:
        with (
            tempfile.TemporaryDirectory() as source_dir_name,
            tempfile.TemporaryDirectory(
                dir=WATCHER_PREANONYMIZED_DROP_DIR
            ) as managed_dir_name,
        ):
            source_dir = Path(source_dir_name)
            managed_dir = Path(managed_dir_name)
            output_dir = managed_dir / "drop"
            output_dir.mkdir()
            persisted_report_dir = managed_dir / "reports"
            persisted_report_dir.mkdir()

            _write_tsv(
                source_dir / "Briefe.txt",
                header=["PatientNr", "FallNr", "dateErstellzeit", "strText"],
                rows=[
                    [
                        "2004",
                        "3004",
                        "2024-05-17 09:30:00",
                        "Already anonymized letter",
                    ],
                    [
                        "2004",
                        "3004",
                        "2024-05-18 10:30:00",
                        "Second anonymized letter",
                    ],
                ],
            )
            _write_tsv(
                source_dir / "Patienten.txt",
                header=["PatientNr", "PatientAlter", "Geschlecht"],
                rows=[["2004", "64", "female"]],
            )
            _write_tsv(
                source_dir / "Diagnosen.txt",
                header=[
                    "PatientNr",
                    "FallNr",
                    "Diagnoseschluessel1",
                    "Diagnosezeit",
                ],
                rows=[["2004", "3004", "K52.9", "2024-05-16 08:00:00"]],
            )
            _write_tsv(
                source_dir / "Labor.txt",
                header=[
                    "PatientNr",
                    "FallNr",
                    "Dokumentzeit",
                    "Leistung",
                    "Leistungstext",
                    "Messwert",
                ],
                rows=[
                    [
                        "2004",
                        "3004",
                        "2024-05-17 06:45:00",
                        "CRP",
                        "C-reactive protein",
                        "4.2",
                    ]
                ],
            )
            _write_tsv(
                source_dir / "Meona.txt",
                header=[
                    "PatientNr",
                    "id",
                    "tradename",
                    "patient_id",
                    "apply_date",
                    "actual_dose",
                    "unit_dose_name",
                    "status",
                ],
                rows=[
                    [
                        "2004",
                        "med-1",
                        "Metamizol",
                        "2004",
                        "2024-05-17 07:00:00",
                        "500",
                        "mg",
                        "given",
                    ]
                ],
            )

            with patch(
                "endoreg_db.services.hub.ingest._processed_report_dir",
                return_value=persisted_report_dir,
            ):
                output = io.StringIO()
                call_command(
                    "import_sap_ish_txt",
                    str(source_dir),
                    output_dir=str(output_dir),
                    source_system="sap_ish_test",
                    center_key=self.center.center_key,
                    process=True,
                    stdout=output,
                )

            self.assertEqual(RawPdfFile.objects.count(), 2)
            reports = list(
                RawPdfFile.objects.select_related("sensitive_meta").order_by("pk")
            )
            self.assertEqual(
                {report.anonymized_text for report in reports},
                {"Already anonymized letter", "Second anonymized letter"},
            )
            self.assertTrue(
                all(report.center_id == self.center.pk for report in reports)
            )
            patient_ids = {
                report.sensitive_meta.pseudo_patient_id
                for report in reports
                if report.sensitive_meta is not None
            }
            examination_ids = {
                report.sensitive_meta.pseudo_examination_id
                for report in reports
                if report.sensitive_meta is not None
            }
            self.assertEqual(len(patient_ids), 1)
            self.assertEqual(len(examination_ids), 1)
            self.assertTrue(
                PatientExternalID.objects.filter(
                    external_id="2004",
                    origin=f"sap_ish_test:{self.center.center_key}",
                ).exists()
            )
            patient_id = patient_ids.pop()
            self.assertEqual(
                PatientDisease.objects.filter(patient_id=patient_id).count(),
                1,
            )
            self.assertEqual(
                PatientLabValue.objects.filter(patient_id=patient_id).count(),
                1,
            )
            self.assertEqual(
                PatientMedication.objects.filter(patient_id=patient_id).count(),
                1,
            )
            self.assertEqual(
                PatientMedicationSchedule.objects.filter(patient_id=patient_id).count(),
                0,
            )
            case = Case.objects.get(patient_id=patient_id)
            self.assertEqual(case.patient_examinations.count(), 1)
            self.assertEqual(case.patient_lab_values.count(), 1)
            self.assertEqual(case.patient_medications.count(), 0)
            self.assertIn("Persisted 2 generated file(s)", output.getvalue())
            self.assertIn("clinical rows=3", output.getvalue())
            self.assertEqual(list(output_dir.glob("*.yaml")), [])

    def test_command_without_process_keeps_valid_yaml_sidecar(self) -> None:
        with (
            tempfile.TemporaryDirectory() as source_dir_name,
            tempfile.TemporaryDirectory(
                dir=WATCHER_PREANONYMIZED_DROP_DIR
            ) as managed_dir_name,
        ):
            source_dir = Path(source_dir_name)
            output_dir = Path(managed_dir_name) / "drop"
            output_dir.mkdir()
            _write_tsv(
                source_dir / "Briefe.txt",
                header=["PatientNr", "FallNr", "dateErstellzeit", "strText"],
                rows=[["2005", "3005", "2024-05-17 09:30:00", "Letter"]],
            )

            call_command(
                "import_sap_ish_txt",
                str(source_dir),
                output_dir=str(output_dir),
                source_system="sap_ish_test",
            )

            sidecars = list(output_dir.glob("*.yaml"))
            self.assertEqual(len(sidecars), 1)
            payload = yaml.safe_load(sidecars[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["external_id"], "2005")
            self.assertEqual(payload["source_system"], "sap_ish_test")
