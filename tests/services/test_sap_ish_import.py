from __future__ import annotations

import json
import stat
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

import yaml
import pytest

from endoreg_db.services.interoperability import sap_ish_import
from endoreg_db.services.interoperability.sap_ish_import import (
    convert_sap_ish_txt_directory_to_preanonymized_drop,
    convert_sap_ish_zip_to_preanonymized_drop,
)
from endoreg_db.services.imports.tabular_import_formats import (
    build_preanonymized_payload,
)
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "entry_name",
    [
        "../escape.txt",
        "/absolute.txt",
        "nested/../../escape.txt",
        "a\\..\\file.txt",
        "C:/file.txt",
    ],
)
def test_zip_rejects_unsafe_paths_and_cleans_workspace(
    tmp_path: Path, entry_name: str
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(entry_name, "private input")
    staging = get_runtime_paths().transcoding
    before = set(staging.glob("sap_ish_import_*"))
    with pytest.raises(ValueError, match="Unsafe SAP archive entry"):
        convert_sap_ish_zip_to_preanonymized_drop(
            zip_path=archive_path, output_dir=tmp_path
        )
    assert set(staging.glob("sap_ish_import_*")) == before
    assert archive_path.exists()


def test_zip_rejects_symbolic_links(tmp_path: Path) -> None:
    archive_path = tmp_path / "link.zip"
    member = zipfile.ZipInfo("link.txt")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(member, "../target.txt")
    with pytest.raises(ValueError, match="Unsafe SAP archive entry"):
        convert_sap_ish_zip_to_preanonymized_drop(
            zip_path=archive_path, output_dir=tmp_path
        )


def test_zip_duplicate_files_fail_and_remove_owned_workspace(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("patienten.txt", "private input")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("patienten.txt", "replacement")
    staging = get_runtime_paths().transcoding
    before = set(staging.glob("sap_ish_import_*"))
    with pytest.raises(FileExistsError):
        convert_sap_ish_zip_to_preanonymized_drop(
            zip_path=archive_path, output_dir=tmp_path
        )
    assert set(staging.glob("sap_ish_import_*")) == before


def _write_tsv(path: Path, *, header: list[str], rows: list[list[str]]) -> None:
    rendered_rows = ["\t".join(header)]
    rendered_rows.extend("\t".join(row) for row in rows)
    path.write_text("\n".join(rendered_rows) + "\n", encoding="utf-8")


def _build_zip_from_directory(source_dir: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w") as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, arcname=file_path.relative_to(source_dir))


def test_build_preanonymized_payload_omits_external_id_pair_without_patient() -> None:
    payload = build_preanonymized_payload(
        {
            "document_type": "labor",
            "canonical_row": {"fall_nr": "3000"},
            "raw_columns": {"FallNr": "3000"},
        },
        source_system="sap_ish_test",
    )

    assert "external_id" not in payload
    assert "external_id_origin" not in payload
    assert payload["source_system"] == "sap_ish_test"
    assert payload["casenumber"] == "3000"


def test_convert_sap_ish_zip_prefers_text_bearing_case_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Path] = []
    original_create = sap_ish_import.atomic_create_file

    def private_create(
        *, destination: Path, content: Iterable[bytes], file_mode: int
    ) -> Path:
        result = original_create(
            destination=destination, content=content, file_mode=file_mode
        )
        assert result.is_relative_to(get_runtime_paths().transcoding.resolve())
        assert result.stat().st_mode & 0o777 == 0o600
        assert result.parent.stat().st_mode & 0o777 == 0o700
        created.append(result)
        return result

    monkeypatch.setattr(sap_ish_import, "atomic_create_file", private_create)
    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        archive_path = temp_dir / "sap_export.zip"
        output_dir = (
            get_runtime_paths().watcher_preanonymized_drop
            / f"service-test-{temp_dir.name}"
        )
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

        assert len(created) == 3
        assert all(not path.parent.exists() for path in created)
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
        output_dir = (
            get_runtime_paths().watcher_preanonymized_drop
            / f"service-test-{temp_dir.name}"
        )
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


def test_convert_sap_ish_txt_directory_writes_yaml_sidecars() -> None:
    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        output_dir = (
            get_runtime_paths().watcher_preanonymized_drop
            / f"service-test-{temp_dir.name}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        _write_tsv(
            source_dir / "briefe.txt",
            header=["PatientNr", "FallNr", "dateErstellzeit", "strText"],
            rows=[["2003", "3003", "2024-05-17 09:30:00", "Anonymized letter"]],
        )
        _write_tsv(
            source_dir / "unsupported.txt",
            header=["UnknownColumn"],
            rows=[["value"]],
        )

        result = convert_sap_ish_txt_directory_to_preanonymized_drop(
            source_dir=source_dir,
            output_dir=output_dir,
            source_system="sap_ish_test",
        )

        assert len(result.generated_files) == 1
        generated_file = result.generated_files[0]
        assert generated_file.sidecar_path.suffix == ".yaml"
        payload = yaml.safe_load(
            generated_file.sidecar_path.read_text(encoding="utf-8")
        )
        assert payload["external_id"] == "2003"
        assert payload["casenumber"] == "3003"
        assert payload["source_system"] == "sap_ish_test"
        assert result.matched_source_files == (source_dir / "briefe.txt",)
        assert result.skipped_source_files == (source_dir / "unsupported.txt",)
