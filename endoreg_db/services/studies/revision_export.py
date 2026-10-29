"""Normalized research tables from explicitly selected immutable examinations."""

import json
from io import BytesIO
from typing import cast

from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.examination_revision import (
    ExaminationRevisionSnapshot,
    SelectedCaseExport,
    SelectedExaminationExport,
)
from lx_dtypes.models.contracts.json_types import JsonObject, JsonScalar, JsonValue
from openpyxl import Workbook

from endoreg_db.services.privacy.response_boundary import (
    reject_transferred_identity_hashes,
)
from endoreg_db.models.administration.case.case import Case
from endoreg_db.models.medical.patient.examination_revision import CaseExaminationRecord
from endoreg_db.services.cases.examination_revisions import (
    assert_case_examinations_complete,
)


def _leaves(
    value: JsonValue, path: str = ""
) -> list[tuple[str, JsonScalar | None, str]]:
    """Lossless long-form scalar table, with explicit null and empty-container rows."""
    if isinstance(value, dict):
        if not value:
            return [(path, None, "empty_object")]
        return [
            row
            for key in sorted(value)
            for row in _leaves(
                value[key], f"{path}/{key.replace('~', '~0').replace('/', '~1')}"
            )
        ]
    if isinstance(value, list):
        if not value:
            return [(path, None, "empty_list")]
        return [
            row
            for index, item in enumerate(value)
            for row in _leaves(item, f"{path}/{index}")
        ]
    kind = (
        "null"
        if value is None
        else "boolean"
        if isinstance(value, bool)
        else "number"
        if isinstance(value, (int, float))
        else "string"
    )
    return [(path, value, kind)]


@transaction.atomic
def selected_case_export(case: Case) -> JsonObject:
    case = Case.objects.select_for_update().get(pk=case.pk)
    assert_case_examinations_complete(case)
    if case.is_deleted or case.patient.is_real_person:
        raise ValueError("Research export requires an available pseudonymous case.")
    records: list[JsonValue] = []
    for selected in (
        CaseExaminationRecord.objects.filter(case=case)
        .select_related("revision__patient_examination", "patient_examination")
        .order_by("patient_examination__date_start", "patient_examination_id")
    ):
        revision = selected.revision
        snapshot = ExaminationRevisionSnapshot.model_validate(revision.snapshot)
        if (
            selected.patient_examination.patient_id != case.patient.pk
            or revision.patient_examination.pk != selected.patient_examination.pk
            or snapshot.content.examination.patient != str(case.patient.pk)
        ):
            raise ValueError("Selected revision ownership does not match the case")
        records.append(
            {
                "patient_examination_id": selected.patient_examination.pk,
                "revision_id": revision.pk,
                "revision_version": revision.version,
                "finalized_at": revision.finalized_at.isoformat()
                if revision.finalized_at
                else None,
                "snapshot": cast(JsonObject, snapshot.model_dump(mode="json")),
            }
        )
    return {
        "schema_version": "2.0",
        "generated_at": timezone.now().isoformat(),
        "case_id": str(case.case_id),
        "patient_id": case.patient.pk,
        "center_key": case.patient.center.center_key if case.patient.center else None,
        "is_closed": case.is_closed,
        "examinations": records,
    }


def case_export_workbook(payload: JsonObject | list[JsonObject]) -> bytes:
    """One row per repeated clinical value; IDs join every sheet without text splitting."""
    reject_transferred_identity_hashes(payload)
    payloads = payload if isinstance(payload, list) else [payload]
    # Validate the complete export before writing any workbook XML. Strict JSON
    # validation accepts serialized UUID/date fields without coercing numeric IDs.
    documents = [
        SelectedCaseExport.model_validate_json(json.dumps(item)) for item in payloads
    ]
    if len({item.case_id for item in documents}) != len(documents):
        raise ValueError("Case identities must be unique in an export")
    revisions: dict[int, SelectedExaminationExport] = {}
    for document in documents:
        for item in document.examinations:
            previous = revisions.setdefault(item.revision_id, item)
            if previous != item:
                raise ValueError("Conflicting content for one revision identity")
        # Excel silently truncates strings beyond its cell limit. Refuse that
        # format rather than returning a workbook that loses clinical content.
        for _, value, _ in _leaves(
            cast(
                JsonObject,
                document.model_dump(
                    mode="json",
                    exclude={
                        "examinations": {
                            "__all__": {"snapshot": {"evidence": {"report_text"}}}
                        }
                    },
                ),
            )
        ):
            if isinstance(value, str) and (
                len(value.encode("utf-16-le")) // 2 > 32767
                or any(ord(char) < 32 and char not in "\t\n\r" for char in value)
            ):
                raise ValueError(
                    "Content cannot be represented losslessly in XLSX; use JSON export"
                )
    payloads = [cast(JsonObject, item.model_dump(mode="json")) for item in documents]
    workbook = Workbook(write_only=True)
    sheets = {
        "Cases": ["case_id", "patient_id", "center_key", "is_closed"],
        "Case examinations": ["case_id", "patient_examination_id", "revision_id"],
        "Examinations": [
            "patient_examination_id",
            "revision_id",
            "revision_version",
            "finalized_at",
            "examination",
            "knowledge_base_module",
            "knowledge_base_version",
        ],
        "Clinical values": ["revision_id", "json_pointer", "value", "value_type"],
        "Medications": [
            "revision_id",
            "item_index",
            "medication",
            "start_at",
            "end_at",
            "unit",
            "active",
        ],
        "Laboratory": [
            "revision_id",
            "item_index",
            "lab_value",
            "timestamp",
            "value",
            "value_str",
            "unit",
        ],
        "Annotations": [
            "revision_id",
            "annotation_id",
            "frame_id",
            "frame_number",
            "presentation_timestamp",
            "stream_time_base_num",
            "stream_time_base_den",
            "artifact_sha256",
            "label_name",
            "value",
            "float_value",
            "annotator",
            "information_source_id",
            "information_source_name",
            "model_meta_id",
        ],
        "Evidence": ["revision_id", "json_pointer", "value", "value_type"],
        "Data dictionary": ["sheet", "column", "meaning"],
        "Metadata": ["property", "value"],
    }
    tables = {name: workbook.create_sheet(name) for name in sheets}

    def append(name: str, values: list[object]) -> None:
        # XLSX preserves numbers and booleans; strings remain text even for formula prefixes.
        from openpyxl.cell import Cell, WriteOnlyCell

        cells: list[Cell] = []
        for value in values:
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise TypeError("Workbook cells must contain scalar values")
            # Excel has only 15 significant decimal digits for numeric cells.
            if isinstance(value, int) and abs(value) >= 10**15:
                value = str(value)
            cell = WriteOnlyCell(tables[name], value=value)
            if isinstance(value, str):
                cell.data_type = "s"
            cells.append(cell)
        tables[name].append(cells)

    for name, columns in sheets.items():
        append(name, list(columns))
        tables[name].freeze_panes = "A2"
    append("Metadata", ["schema_version", "1.0"])
    append(
        "Metadata",
        ["large_integers", "Integers with more than 15 digits are exact decimal text."],
    )
    append("Metadata", ["generated_at", timezone.now().isoformat()])
    append(
        "Metadata",
        [
            "selection",
            "Only explicit finalized examination revisions; alternatives excluded",
        ],
    )
    append(
        "Metadata",
        [
            "media",
            "Content-addressed references only; processed files use existing authorized download/export routes",
        ],
    )
    seen_revisions: set[int] = set()
    for payload in payloads:
        append("Cases", [payload[key] for key in sheets["Cases"]])
        records = payload["examinations"]
        if not isinstance(records, list):
            raise ValueError("examinations must be a list")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("examination export record must be an object")
            if not isinstance(record["revision_id"], int):
                raise ValueError("revision_id must be an integer")
            append(
                "Case examinations",
                [
                    payload["case_id"],
                    record["patient_examination_id"],
                    record["revision_id"],
                ],
            )
            if record["revision_id"] in seen_revisions:
                continue
            seen_revisions.add(record["revision_id"])
            snapshot = ExaminationRevisionSnapshot.model_validate(record["snapshot"])
            clinical = snapshot.content.examination
            revision_id = record["revision_id"]
            append(
                "Examinations",
                [
                    record["patient_examination_id"],
                    revision_id,
                    record["revision_version"],
                    record["finalized_at"],
                    clinical.examination,
                    clinical.knowledge_base_module,
                    clinical.knowledge_base_version,
                ],
            )
            # Includes descriptors, repeated findings, indications, dosage and normal ranges.
            for path, value, kind in _leaves(
                cast(JsonObject, snapshot.content.model_dump(mode="json"))
            ):
                append("Clinical values", [revision_id, path, value, kind])
            for index, medication in enumerate(snapshot.content.medications):
                row = medication.model_dump(mode="json")
                append(
                    "Medications",
                    [
                        revision_id,
                        index,
                        *[row[key] for key in sheets["Medications"][2:]],
                    ],
                )
            for index, lab in enumerate(snapshot.content.lab_values):
                row = lab.model_dump(mode="json")
                append(
                    "Laboratory",
                    [
                        revision_id,
                        index,
                        *[row[key] for key in sheets["Laboratory"][2:]],
                    ],
                )
            for annotation in snapshot.evidence.annotations:
                row = annotation.model_dump(mode="json")
                append(
                    "Annotations",
                    [revision_id, *[row[key] for key in sheets["Annotations"][1:]]],
                )
            for path, value, kind in _leaves(
                cast(
                    JsonObject,
                    snapshot.evidence.model_dump(
                        mode="json", exclude={"annotations", "report_text"}
                    ),
                )
            ):
                append("Evidence", [revision_id, path, value, kind])
    for name, columns in sheets.items():
        for column in columns:
            meaning = (
                "RFC 6901 path into the immutable snapshot; list indices preserve repetitions"
                if column == "json_pointer"
                else "Explicit null/empty markers; empty cells never mean zero"
                if column == "value_type"
                else "Foreign key to Examinations.revision_id"
                if column == "revision_id"
                else "Canonical snake_case contract field"
            )
            append("Data dictionary", [name, column, meaning])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
