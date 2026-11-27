from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind
from lx_dtypes.models.contracts.table_import import TableSource
from lx_dtypes.terminology.terminology_loader import load_module_kb
from rest_framework.test import APIClient

from endoreg_db.services.studies.table_parser import (
    extract_table_records,
    inspect_table,
    parse_table_mapping,
    spacy_patient_extractor,
)
from endoreg_db.services.studies.table_prefill import (
    TablePrefill,
    default_prefill_rules,
)

SEMICOLON = TableSource(delimiter=";")
HEADERS = (
    "record_id;Patient.Vorname;Patient.Nachname;Geburtsdatum;Geschlecht;"
    "Untersuchungsdatum;Befund\n"
    "1;Erika;Mustermann;01.02.1980;w;2026-01-10;Polyp\n"
    "2;Max;Mustermann;03.04.1975;m;2026-01-11;\n"
).encode()
PATIENT_LINES = (
    "Fall;Patientenzeile;Datum;Geschlecht\n"
    "a1;Patient: Mustermann, Erika, geb. 01.02.1980;10.01.2026;w\n"
    "a2;Patient: Beispiel, Anna, geb. 05.06.1990;11.01.2026;w\n"
    "a3;Patient: Muster, Max, geb. 03.04.1975;12.01.2026;m\n"
).encode()


def _fields(
    prefill: TablePrefill | None,
) -> dict[str, tuple[str, str | None, str | None]]:
    assert prefill is not None
    return {
        item.field: (item.column, item.extractor, item.date_format)
        for item in prefill.fields
    }


def _never_called(text: str) -> Mapping[str, object]:
    raise AssertionError("Header matches must not run the extractor")


def test_headers_and_date_formats_prefill_without_spacy() -> None:
    inspection = inspect_table(
        HEADERS,
        ".csv",
        SEMICOLON,
        prefill_rules=default_prefill_rules(),
        patient_extractor=_never_called,
    )

    assert _fields(inspection.prefill) == {
        "row_id": ("record_id", None, None),
        "first_name": ("Patient.Vorname", None, None),
        "last_name": ("Patient.Nachname", None, None),
        "dob": ("Geburtsdatum", None, "%d.%m.%Y"),
        "gender": ("Geschlecht", None, None),
        # ISO cells need no format.
        "examination_date": ("Untersuchungsdatum", None, None),
        "finding": ("Befund", None, None),
    }
    assert inspection.prefill is not None
    assert inspection.prefill.patient_line_detection == "not_needed"
    spellings = {
        (item.spelling, item.identifier) for item in inspection.prefill.gender_spellings
    }
    assert {("w", "female"), ("m", "male")} <= spellings
    assert inspection.values == []


def test_spacy_detects_patient_line_and_the_prefill_imports(
    packaged_registry: Path,
) -> None:
    del packaged_registry
    inspection = inspect_table(
        PATIENT_LINES,
        ".csv",
        SEMICOLON,
        prefill_rules=default_prefill_rules(),
        patient_extractor=spacy_patient_extractor,
    )

    fields = _fields(inspection.prefill)
    assert inspection.prefill is not None
    assert inspection.prefill.patient_line_detection == "found"
    assert inspection.prefill.patient_line_column == "Patientenzeile"
    assert fields["first_name"] == ("Patientenzeile", "spacy_patient", None)
    assert fields["last_name"] == ("Patientenzeile", "spacy_patient", None)
    assert fields["dob"] == ("Patientenzeile", "spacy_patient", None)
    # Gender stays an explicit column; the extractor only guesses it from names.
    assert fields["gender"] == ("Geschlecht", None, None)
    assert fields["row_id"] == ("Fall", None, None)
    assert fields["examination_date"] == ("Datum", None, "%d.%m.%Y")

    spec = parse_table_mapping(
        """
schema_version: '1.0'
mapping_id: 8d2c4f6a-1b3e-4a5c-9d7f-0e1a2b3c4d5e
name: Patient line prefill
knowledge_base:
  knowledge_base_module: dgvs_reporting
  knowledge_base_version: 0.1.0
source: {delimiter: ';'}
row_id: Fall
first_name: {columns: [Patientenzeile], extractor: spacy_patient, output: first_name}
last_name: {columns: [Patientenzeile], extractor: spacy_patient, output: last_name}
dob: {columns: [Patientenzeile], extractor: spacy_patient, output: dob}
gender: {columns: [Geschlecht], values: {w: female, m: male}}
examination_date: {columns: [Datum], date_format: '%d.%m.%Y'}
examination: {value: colonoscopy}
"""
    )
    records = extract_table_records(spec, PATIENT_LINES, ".csv")
    assert [
        (record.patient.last_name, record.patient.first_name, str(record.patient.dob))
        for record in records
    ] == [
        ("Mustermann", "Erika", "1980-02-01"),
        ("Beispiel", "Anna", "1990-06-05"),
        ("Muster", "Max", "1975-04-03"),
    ]


def _comma_names(text: str) -> Mapping[str, object]:
    last, _, rest = text.partition(",")
    first = rest.split(",")[0].strip()
    return {"first_name": first or "unknown", "last_name": last.strip() or "unknown"}


def test_generic_name_column_holding_the_whole_line_uses_the_extractor() -> None:
    table = (
        "id;Name;Datum\n"
        "1;Mustermann, Erika, geb. 01.02.1980;10.01.2026\n"
        "2;Beispiel, Anna, geb. 05.06.1990;11.01.2026\n"
    ).encode()
    inspection = inspect_table(
        table,
        ".csv",
        SEMICOLON,
        prefill_rules=default_prefill_rules(),
        patient_extractor=_comma_names,
    )

    fields = _fields(inspection.prefill)
    assert fields["last_name"] == ("Name", "spacy_patient", None)
    assert fields["first_name"] == ("Name", "spacy_patient", None)


def test_unrecognized_or_unavailable_extraction_keeps_header_prefill() -> None:
    def placeholders(text: str) -> Mapping[str, object]:
        return {"first_name": "unknown", "last_name": "unknown"}

    def missing_model(text: str) -> Mapping[str, object]:
        raise OSError("spaCy model is not installed")

    table = "id;Freitext;Datum\n1;Polyp im Sigma 5 mm;2026-01-10\n2;Kein Befund heute;2026-01-11\n".encode()
    for extractor, detection in (
        (placeholders, "not_found"),
        (missing_model, "unavailable"),
    ):
        inspection = inspect_table(
            table,
            ".csv",
            SEMICOLON,
            prefill_rules=default_prefill_rules(),
            patient_extractor=extractor,
        )
        assert inspection.prefill is not None
        assert inspection.prefill.patient_line_detection == detection
        assert _fields(inspection.prefill) == {
            "row_id": ("id", None, None),
            "examination_date": ("Datum", None, None),
        }


def test_default_rules_are_packaged_and_valid() -> None:
    rules = default_prefill_rules()
    assert rules.patient_line.extractor == "spacy_patient"
    assert "gender" not in rules.patient_line.outputs
    assert set(rules.fields) == {
        "row_id",
        "first_name",
        "last_name",
        "dob",
        "gender",
        "examination_date",
        "examination",
        "finding",
    }


def test_packaged_gender_identifiers_exist_in_reference_catalogue(
    packaged_registry: Path,
) -> None:
    knowledge_base = load_module_kb("endoreg_reference", version="2.0.0")
    catalogue_genders = {
        record.key[1]
        for catalog in knowledge_base.reference_catalog.values()
        for record in catalog.payload.records
        if record.key[0] is ReferenceKind.GENDER
    }
    template = parse_table_mapping(
        (files("endoreg_db") / "data/study_import/colonoscopy.yml").read_text(
            encoding="utf-8"
        )
    )
    assert catalogue_genders
    assert set(default_prefill_rules().gender_values) <= catalogue_genders
    assert set(template.gender.values.values()) <= catalogue_genders


@pytest.mark.django_db
def test_inspection_view_prefill_returns_columns_but_no_cells(
    packaged_registry: Path,
) -> None:
    del packaged_registry
    client = APIClient()
    client.force_authenticate(
        User.objects.create_user(username="prefill", is_superuser=True)
    )

    response = client.post(
        "/api/media/studies/table-mappings/inspect/",
        {
            "file": SimpleUploadedFile(
                "lines.csv", PATIENT_LINES, content_type="text/csv"
            ),
            "options": '{"source": {"delimiter": ";"}, "prefill": true}',
        },
        format="multipart",
    )

    assert response.status_code == 200, response.content
    prefill = response.json()["prefill"]
    assert prefill["patient_line_column"] == "Patientenzeile"
    assert {
        "field": "dob",
        "column": "Patientenzeile",
        "extractor": "spacy_patient",
        "output": "dob",
        "date_format": None,
    } in prefill["fields"]
    for value in (b"Mustermann", b"Erika", b"1980"):
        assert value not in response.content
    without = client.post(
        "/api/media/studies/table-mappings/inspect/",
        {
            "file": SimpleUploadedFile("lines.csv", PATIENT_LINES),
            "options": '{"source": {"delimiter": ";"}}',
        },
        format="multipart",
    )
    assert without.json()["prefill"] is None
