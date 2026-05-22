from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from endoreg_db.services.sap_ish_import import (
    convert_sap_ish_zip_to_preanonymized_drop,
)
from endoreg_db.utils.filesystem.paths import WATCHER_PREANONYMIZED_DROP_DIR


def _write_tsv(path: Path, *, header: list[str], rows: list[list[str]]) -> None:
    rendered_rows = ["\t".join(header)]
    rendered_rows.extend("\t".join(row) for row in rows)
    path.write_text("\n".join(rendered_rows) + "\n", encoding="utf-8")


def _build_zip_from_directory(source_dir: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w") as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, arcname=file_path.relative_to(source_dir))


def test_convert_sap_ish_zip_prefers_text_bearing_case_rows() -> None:
    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        archive_path = temp_dir / "sap_export.zip"
        output_dir = WATCHER_PREANONYMIZED_DROP_DIR / f"service-test-{temp_dir.name}"
        output_dir.mkdir(parents=True, exist_ok=True)

        _write_tsv(
            source_dir / "briefe.txt",
            header=["PatientNr", "FallNr", "dateErstellzeit", "strText"],
            rows=[["2001", "3001", "2024-05-17 09:30:00", "Already anonymized letter"]],
        )
        _write_tsv(
            source_dir / "patienten.txt",
            header=["PatientNr", "PatientAlter", "Geschlecht"],
            rows=[["2001", "64", "female"]],
        )
        _write_tsv(
            source_dir / "diagnosen.txt",
            header=["PatientNr", "FallNr", "Diagnoseschluessel1", "Diagnosezeit"],
            rows=[["2001", "3001", "K52.9", "2024-05-16 08:00:00"]],
        )
        _build_zip_from_directory(source_dir, archive_path)

        result = convert_sap_ish_zip_to_preanonymized_drop(
            zip_path=archive_path,
            output_dir=output_dir,
            source_system="sap_ish_test",
            center_name="test-center",
        )

        assert len(result.generated_files) == 1
        generated_file = result.generated_files[0]
        payload = json.loads(generated_file.sidecar_path.read_text(encoding="utf-8"))

        assert payload["external_id"] == "2001"
        assert payload["casenumber"] == "3001"
        assert payload["patient_gender"] == "female"
        assert payload["anonymized_text"] == "Already anonymized letter"
        assert payload["source_document_type"] == "briefe"
        assert payload["source_system"] == "sap_ish_test"
        assert payload["center_name"] == "test-center"
        assert "related_rows_by_type" in payload["raw_columns"]
        assert "diagnosen" in payload["raw_columns"]["related_rows_by_type"]
        assert generated_file.carrier_path.read_text(encoding="utf-8").strip() == (
            "Already anonymized letter"
        )


def test_convert_sap_ish_zip_builds_case_summary_when_no_text_rows_exist() -> None:
    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        archive_path = temp_dir / "sap_export.zip"
        output_dir = WATCHER_PREANONYMIZED_DROP_DIR / f"service-test-{temp_dir.name}"
        output_dir.mkdir(parents=True, exist_ok=True)

        _write_tsv(
            source_dir / "labor.txt",
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
                    "2002",
                    "3002",
                    "2024-05-17 06:45:00",
                    "CRP",
                    "C-reactive protein",
                    "4.2",
                ]
            ],
        )
        _write_tsv(
            source_dir / "bewegungen.txt",
            header=[
                "PatientNr",
                "FallNr",
                "Zugangszeit",
                "Behandlungsort",
                "Fachabteilung",
                "Zimmer",
            ],
            rows=[["2002", "3002", "2024-05-17 06:30:00", "Ward A", "GI", "12"]],
        )
        _write_tsv(
            source_dir / "patienten.txt",
            header=["PatientNr", "PatientAlter", "Geschlecht"],
            rows=[["2002", "51", "male"]],
        )
        _build_zip_from_directory(source_dir, archive_path)

        result = convert_sap_ish_zip_to_preanonymized_drop(
            zip_path=archive_path,
            output_dir=output_dir,
            source_system="sap_ish_test",
        )

        assert len(result.generated_files) == 1
        generated_file = result.generated_files[0]
        payload = json.loads(generated_file.sidecar_path.read_text(encoding="utf-8"))
        carrier_text = generated_file.carrier_path.read_text(encoding="utf-8")

        assert payload["external_id"] == "2002"
        assert payload["casenumber"] == "3002"
        assert payload["patient_gender"] == "male"
        assert payload["source_document_type"] == "labor"
        assert payload["examination_date"] == "2024-05-17"
        assert payload["anonymized_text"].startswith(
            "Case summary generated from SAP IS-H tabular export"
        )
        assert "labor: 1 row(s)" in payload["anonymized_text"]
        assert "bewegungen: 1 row(s)" in payload["anonymized_text"]
        assert carrier_text.strip() == payload["anonymized_text"]
