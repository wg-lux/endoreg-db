from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.models.contracts.table_import import TableExtractionError, TableSource
from openpyxl import Workbook
from rest_framework.test import APIClient

from endoreg_db.models.other.gender import Gender
from endoreg_db.services.studies.table_mappings import mapping_vocabulary
from endoreg_db.services.studies.table_parser import (
    MAX_INSPECTED_VALUES,
    extract_table_records,
    inspect_table,
    parse_table_mapping,
)

CSV = (
    "id;first;last;dob;sex;date;finding\n"
    "1;Erika;Mustermann;01.02.1980;w;10.01.2026;Polyp\n"
    "2;Max;Mustermann;03.04.1975;m;11.01.2026;\n"
    "3;Anna;Beispiel;05.06.1990;w;12.01.2026;Polyp\n"
).encode("utf-8")
SEMICOLON = TableSource(delimiter=";")
INSPECT_URL = "/api/media/studies/table-mappings/inspect/"


def test_inspection_reads_header_and_requested_distinct_values() -> None:
    inspection = inspect_table(CSV, ".csv", SEMICOLON, value_columns=["sex", "finding"])
    values = {item.column: item for item in inspection.values}

    assert inspection.columns == [
        "id",
        "first",
        "last",
        "dob",
        "sex",
        "date",
        "finding",
    ]
    assert inspection.row_count == 3
    assert values["sex"].values == ["m", "w"]
    assert values["finding"].values == ["Polyp"]
    # Identity columns are never returned unless explicitly requested.
    assert [item.column for item in inspection.values] == ["sex", "finding"]
    assert not inspection.sheet_required


def test_inspected_values_are_valid_mapping_keys(packaged_registry: Path) -> None:
    del packaged_registry
    inspection = inspect_table(CSV, ".csv", SEMICOLON, value_columns=["sex", "finding"])
    genders = {"w": "female", "m": "male"}
    spec = parse_table_mapping(
        f"""
schema_version: '1.0'
mapping_id: 0b4f6f0e-5b8c-4c5e-8f3d-2d8d4a6f9a11
name: Inspected mapping
knowledge_base:
  knowledge_base_module: dgvs_reporting
  knowledge_base_version: 0.1.0
source: {{delimiter: ';'}}
row_id: id
first_name: first
last_name: last
dob: {{columns: [dob], date_format: '%d.%m.%Y'}}
gender: {{columns: [sex], values: {{{", ".join(f"{key}: {genders[key]}" for key in inspection.values[0].values)}}}}}
examination_date: {{columns: [date], date_format: '%d.%m.%Y'}}
examination: {{value: colonoscopy}}
finding: {{columns: [finding], required: false, values: {{Polyp: colon_polyp}}}}
"""
    )

    records = extract_table_records(spec, CSV, ".csv")

    assert [record.patient.gender for record in records] == ["female", "male", "female"]
    assert [len(record.record.patient_findings) for record in records] == [1, 0, 1]


def _workbook(*sheets: str) -> bytes:
    workbook = Workbook()
    first = workbook.active
    assert first is not None
    first.title = sheets[0]
    for name in sheets[1:]:
        workbook.create_sheet(name)
    target = workbook[sheets[-1]]
    target.append(["id", "date", "finding"])
    target.append([7, datetime(2026, 1, 10), "Polyp"])
    content = io.BytesIO()
    workbook.save(content)
    return content.getvalue()


def test_workbooks_require_a_sheet_choice_and_normalize_cells() -> None:
    content = _workbook("Notes", "Export")

    pending = inspect_table(content, ".xlsx", TableSource())
    assert pending.sheet_required
    assert pending.sheets == ["Notes", "Export"]
    assert pending.columns == []

    inspection = inspect_table(
        content, ".xlsx", TableSource(sheet="Export"), value_columns=["id", "date"]
    )
    assert inspection.columns == ["id", "date", "finding"]
    # The same normalization as the importer: integral numbers and ISO dates.
    assert [item.values for item in inspection.values] == [["7"], ["2026-01-10"]]


def test_inspection_bounds_and_rejections() -> None:
    rows = "".join(f"{index};v{index}\n" for index in range(MAX_INSPECTED_VALUES + 5))
    many = f"id;code\n{rows}1000;{'x' * 300}\n".encode()
    inspection = inspect_table(many, ".csv", SEMICOLON, value_columns=["code"])
    (code,) = inspection.values
    assert len(code.values) == MAX_INSPECTED_VALUES
    assert code.truncated
    assert all(len(value) <= 255 for value in code.values)

    with pytest.raises(TableExtractionError) as missing:
        inspect_table(CSV, ".csv", SEMICOLON, value_columns=["Erika"])
    assert missing.value.code == "source_column_missing"
    with pytest.raises(ValueError, match="at most 50"):
        inspect_table(CSV, ".csv", SEMICOLON, value_columns=["sex", "sex"])
    with pytest.raises(TableExtractionError) as header:
        inspect_table(b"id;id\n1;2\n", ".csv", SEMICOLON)
    assert header.value.code == "empty_or_duplicate_header"
    with pytest.raises(ValueError, match="Supported table formats"):
        inspect_table(CSV, ".ods", SEMICOLON)


def test_vocabulary_lists_template_identifiers(packaged_registry: Path) -> None:
    del packaged_registry
    vocabulary = mapping_vocabulary(
        KnowledgeBaseIdentity(
            knowledge_base_module="dgvs_reporting", knowledge_base_version="0.1.0"
        )
    )

    examinations = {item.name: item.findings for item in vocabulary.examinations}
    assert "colon_polyp" in examinations["colonoscopy"]
    findings = {item.name for item in vocabulary.findings}
    assert "colon_polyp" in findings
    classifications = {item.name: item.choices for item in vocabulary.classifications}
    assert all(choices == sorted(choices) for choices in classifications.values())


@pytest.mark.django_db
def test_inspection_view_returns_columns_values_and_vocabulary(
    packaged_registry: Path,
) -> None:
    del packaged_registry
    Gender.objects.get_or_create(name="female")
    client = APIClient()
    client.force_authenticate(
        User.objects.create_user(username="mapper", is_superuser=True)
    )

    response = client.post(
        INSPECT_URL,
        {
            "file": SimpleUploadedFile("table.csv", CSV, content_type="text/csv"),
            "options": '{"source": {"delimiter": ";"}, "value_columns": ["sex"]}',
        },
        format="multipart",
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["columns"][4] == "sex"
    assert body["values"] == [
        {"column": "sex", "values": ["m", "w"], "truncated": False}
    ]
    assert "female" in body["vocabulary"]["genders"]
    assert (
        body["vocabulary"]["knowledge_base"]["knowledge_base_module"]
        == "dgvs_reporting"
    )
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.django_db
def test_inspection_view_errors_and_permissions(packaged_registry: Path) -> None:
    del packaged_registry
    client = APIClient()
    client.force_authenticate(User.objects.create_user(username="reader"))
    denied = client.post(
        INSPECT_URL,
        {"file": SimpleUploadedFile("table.csv", CSV, content_type="text/csv")},
        format="multipart",
    )
    assert denied.status_code == 403

    client.force_authenticate(
        User.objects.create_user(username="admin", is_superuser=True)
    )
    wrong_type = client.post(
        INSPECT_URL,
        {"file": SimpleUploadedFile("table.ods", CSV)},
        format="multipart",
    )
    assert wrong_type.status_code == 400
    bad_options = client.post(
        INSPECT_URL,
        {
            "file": SimpleUploadedFile("table.csv", CSV),
            "options": '{"source": {"delimiter": ";;"}}',
        },
        format="multipart",
    )
    assert bad_options.status_code == 400
    missing_column = client.post(
        INSPECT_URL,
        {
            "file": SimpleUploadedFile("table.csv", CSV),
            "options": '{"source": {"delimiter": ";"}, "value_columns": ["Mustermann"]}',
        },
        format="multipart",
    )
    assert missing_column.status_code == 400
    assert missing_column.json()["code"] == "source_column_missing"
    # Column names the caller sent are echoed nowhere; cell values never are.
    assert b"Mustermann" not in missing_column.content
    assert b"Erika" not in missing_column.content


def test_assistant_json_document_parses_and_imports(packaged_registry: Path) -> None:
    """The lx-annotate mapping assistant submits JSON, which is valid setup YAML."""
    del packaged_registry
    document = {
        "schema_version": "1.0",
        "mapping_id": "6f1c2a7e-2f43-4d1b-9c55-0e0e5d1f7a20",
        "name": "Assistant export",
        "knowledge_base": {
            "knowledge_base_module": "dgvs_reporting",
            "knowledge_base_version": "0.1.0",
        },
        "source": {"delimiter": ";", "encoding": "utf-8-sig", "header_row": 1},
        "row_id": {"columns": ["id"]},
        "first_name": {"columns": ["first"]},
        "last_name": {"columns": ["last"]},
        "dob": {"columns": ["dob"], "date_format": "%d.%m.%Y"},
        "examination_date": {"columns": ["date"], "date_format": "%d.%m.%Y"},
        "gender": {"columns": ["sex"], "values": {"m": "male", "w": "female"}},
        "examination": {"value": "colonoscopy"},
        "findings": {
            "finding_1": {
                "finding": {
                    "columns": ["finding"],
                    "values": {"Polyp": "colon_polyp"},
                    "required": False,
                },
                "classifications": {},
            }
        },
    }

    spec = parse_table_mapping(json.dumps(document, indent=2))
    records = extract_table_records(spec, CSV, ".csv")

    assert [record.patient.gender for record in records] == ["female", "male", "female"]
    assert [
        [finding.finding for finding in record.record.patient_findings]
        for record in records
    ] == [["colon_polyp"], [], ["colon_polyp"]]
