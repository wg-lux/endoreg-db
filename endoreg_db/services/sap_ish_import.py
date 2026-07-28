from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from collections.abc import Callable
from typing import Any

from endoreg_db.services.tabular_import_formats import (
    build_preanonymized_payload,
    normalize_document_row,
    resolve_document_template,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
)
from endoreg_db.utils.paths import ensure_within_data_root
from lx_dtypes.models.contracts.sap_ish_import import (
    SapIshImportPayload,
    SapIshImportPayloadValue,
    SapIshDropFilePayload,
    dump_sap_ish_drop_file_payload,
    validate_sap_ish_drop_file_payload,
)

_validate_sap_ish_drop_file_payload: Callable[
    [SapIshImportPayload],
    SapIshDropFilePayload,
] = validate_sap_ish_drop_file_payload

TEXT_DOCUMENT_TYPES = ("cwd", "briefe", "radiologie")
ANCHOR_DOCUMENT_TYPES = (
    "cwd",
    "briefe",
    "radiologie",
    "pathodocs",
    "prozeduren",
    "diagnosen",
    "labor",
    "bewegungen",
    "meona_medikamente",
    "stammdaten",
    "patienten",
)
TIMESTAMP_FIELDS = (
    "dokumentzeit",
    "date_erstellzeit",
    "zugangszeit",
    "diagnosezeit",
    "beginnzeit",
    "apply_date",
    "prepare_date",
    "creation_date",
)


@dataclass(frozen=True, slots=True)
class SapIshNormalizedRow:
    document_type: str
    canonical_row: dict[str, Any]
    raw_columns: dict[str, Any]
    unknown_columns: dict[str, Any]
    source_path: Path
    row_number: int


@dataclass(frozen=True, slots=True)
class GeneratedDropFile:
    carrier_path: Path
    sidecar_path: Path
    payload: SapIshDropFilePayload
    document_type: str


@dataclass(frozen=True, slots=True)
class SapIshImportResult:
    generated_files: tuple[GeneratedDropFile, ...]
    matched_source_files: tuple[Path, ...]
    skipped_source_files: tuple[Path, ...]


def _read_text_with_fallbacks(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _read_tsv_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    content = _read_text_with_fallbacks(path)
    reader = csv.DictReader(io.StringIO(content), delimiter="\t")
    if not reader.fieldnames:
        raise ValueError(f"No header row found in {path}")

    rows: list[tuple[int, dict[str, Any]]] = []
    for row_number, row in enumerate(reader, start=2):
        cleaned_row = {
            str(key): value
            for key, value in row.items()
            if key is not None and str(key).strip()
        }
        if not any(
            isinstance(value, str) and value.strip() or value not in (None, "")
            for value in cleaned_row.values()
        ):
            continue
        rows.append((row_number, cleaned_row))
    return rows


def _normalize_supported_rows(
    source_dir: Path,
) -> tuple[list[SapIshNormalizedRow], list[Path], list[Path]]:
    normalized_rows: list[SapIshNormalizedRow] = []
    matched_files: list[Path] = []
    skipped_files: list[Path] = []

    for source_path in sorted(source_dir.rglob("*.txt")):
        try:
            file_rows = _read_tsv_rows(source_path)
            if not file_rows:
                skipped_files.append(source_path)
                continue
            template_match = resolve_document_template(tuple(file_rows[0][1].keys()))
        except ValueError:
            skipped_files.append(source_path)
            continue

        matched_files.append(source_path)
        for row_number, row in file_rows:
            normalized_document = normalize_document_row(
                row,
                template_match=template_match,
            )
            normalized_rows.append(
                SapIshNormalizedRow(
                    document_type=normalized_document["document_type"],
                    canonical_row=dict(normalized_document["canonical_row"]),
                    raw_columns=dict(normalized_document["raw_columns"]),
                    unknown_columns=dict(normalized_document["unknown_columns"]),
                    source_path=source_path,
                    row_number=row_number,
                )
            )
    return normalized_rows, matched_files, skipped_files


def _extract_case_key(row: SapIshNormalizedRow) -> tuple[str, str] | None:
    patient_nr = row.canonical_row.get("patient_nr")
    fall_nr = row.canonical_row.get("fall_nr")
    if (
        isinstance(patient_nr, str)
        and patient_nr
        and isinstance(fall_nr, str)
        and fall_nr
    ):
        return patient_nr, fall_nr
    return None


def _extract_patient_key(row: SapIshNormalizedRow) -> str | None:
    patient_nr = row.canonical_row.get("patient_nr") or row.canonical_row.get(
        "source_patient_id"
    )
    if isinstance(patient_nr, str) and patient_nr:
        return patient_nr
    return None


def _extract_row_timestamp(row: SapIshNormalizedRow) -> datetime | None:
    for field_name in TIMESTAMP_FIELDS:
        value = row.canonical_row.get(field_name)
        if isinstance(value, datetime):
            return value
    return None


def _extract_preferred_text(row: SapIshNormalizedRow) -> str | None:
    for field_name in ("pmd_anam", "str_text", "befund", "kurbefund", "beurteilung"):
        value = row.canonical_row.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pick_anchor_row(rows: list[SapIshNormalizedRow]) -> SapIshNormalizedRow:
    order_lookup = {
        document_type: index
        for index, document_type in enumerate(ANCHOR_DOCUMENT_TYPES)
    }
    return sorted(
        rows,
        key=lambda row: (
            order_lookup.get(row.document_type, len(order_lookup)),
            _extract_row_timestamp(row) or datetime.max,
            row.source_path.name,
            row.row_number,
        ),
    )[0]


def _case_header_lines(anchor_row: SapIshNormalizedRow) -> list[str]:
    patient_nr = anchor_row.canonical_row.get("patient_nr")
    fall_nr = anchor_row.canonical_row.get("fall_nr")
    if not patient_nr and not fall_nr:
        return []
    lines = ["Case summary generated from SAP IS-H tabular export"]
    if patient_nr:
        lines.append(f"PatientNr: {patient_nr}")
    if fall_nr:
        lines.append(f"FallNr: {fall_nr}")
    return lines


def _diagnosis_details(row: SapIshNormalizedRow) -> list[str]:
    diagnosis_key = row.canonical_row.get("diagnoseschluessel_1")
    return [f"code={diagnosis_key}"] if diagnosis_key else []


def _laboratory_details(row: SapIshNormalizedRow) -> list[str]:
    details: list[str] = []
    test_name = row.canonical_row.get("leistungstext") or row.canonical_row.get(
        "leistung"
    )
    measurement = row.canonical_row.get("messwert")
    if test_name:
        details.append(str(test_name))
    if measurement:
        details.append(f"value={measurement}")
    return details


def _procedure_details(row: SapIshNormalizedRow) -> list[str]:
    operation_code = row.canonical_row.get("op_code")
    return [f"op_code={operation_code}"] if operation_code else []


def _movement_details(row: SapIshNormalizedRow) -> list[str]:
    details: list[str] = []
    for value in (
        row.canonical_row.get("behandlungsort"),
        row.canonical_row.get("fachabteilung"),
    ):
        if value:
            details.append(str(value))
    room = row.canonical_row.get("zimmer")
    if room:
        details.append(f"room={room}")
    return details


def _pathology_document_details(row: SapIshNormalizedRow) -> list[str]:
    details: list[str] = []
    document_id = row.canonical_row.get("dokumentnummer")
    document_type_id = row.canonical_row.get("dokumenttyp_id")
    if document_id:
        details.append(f"document_id={document_id}")
    if document_type_id:
        details.append(f"type={document_type_id}")
    return details


def _medication_details(row: SapIshNormalizedRow) -> list[str]:
    details: list[str] = []
    trade_name = row.canonical_row.get("tradename")
    dose = row.canonical_row.get("actual_dose")
    unit = row.canonical_row.get("unit_dose_name")
    if trade_name:
        details.append(str(trade_name))
    if dose:
        details.append(f"dose={dose}")
    if unit:
        details.append(str(unit))
    return details


CASE_DETAIL_BUILDERS: dict[str, Callable[[SapIshNormalizedRow], list[str]]] = {
    "diagnosen": _diagnosis_details,
    "labor": _laboratory_details,
    "prozeduren": _procedure_details,
    "bewegungen": _movement_details,
    "pathodocs": _pathology_document_details,
    "meona_medikamente": _medication_details,
}


def _case_row_details(row: SapIshNormalizedRow) -> list[str]:
    details: list[str] = []
    timestamp = _extract_row_timestamp(row)
    if timestamp is not None:
        details.append(timestamp.isoformat(sep=" ", timespec="seconds"))
    builder = CASE_DETAIL_BUILDERS.get(row.document_type)
    if builder is not None:
        details.extend(builder(row))
    return details


def _document_summary_lines(
    document_type: str,
    rows: list[SapIshNormalizedRow],
) -> list[str]:
    if not rows:
        return []
    lines = [f"{document_type}: {len(rows)} row(s)"]
    for row in rows[:5]:
        details = _case_row_details(row)
        if details:
            lines.append(f"- {' | '.join(details)}")
    return lines


def _build_case_summary(rows: list[SapIshNormalizedRow]) -> str:
    anchor_row = _pick_anchor_row(rows)
    lines = _case_header_lines(anchor_row)
    grouped_rows: dict[str, list[SapIshNormalizedRow]] = defaultdict(list)
    for row in rows:
        grouped_rows[row.document_type].append(row)
    for document_type in ANCHOR_DOCUMENT_TYPES:
        lines.extend(
            _document_summary_lines(
                document_type,
                grouped_rows.get(document_type, []),
            )
        )
    return "\n".join(lines).strip()


def _build_enriched_raw_columns(
    *,
    primary_row: SapIshNormalizedRow,
    related_rows: list[SapIshNormalizedRow],
) -> dict[str, Any]:
    rows_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_files: list[str] = []
    for row in related_rows:
        rows_by_type[row.document_type].append(
            {
                "source_file": row.source_path.name,
                "row_number": row.row_number,
                "raw_columns": row.raw_columns,
                "unknown_columns": row.unknown_columns,
            }
        )
        source_files.append(row.source_path.name)

    return {
        "primary_row": {
            "document_type": primary_row.document_type,
            "source_file": primary_row.source_path.name,
            "row_number": primary_row.row_number,
            "raw_columns": primary_row.raw_columns,
        },
        "related_rows_by_type": dict(rows_by_type),
        "source_files": sorted(set(source_files)),
    }


def _derive_patient_gender(rows: list[SapIshNormalizedRow]) -> str | None:
    for row in rows:
        gender = row.canonical_row.get("geschlecht")
        if isinstance(gender, str) and gender.strip():
            return gender.strip()
    return None


def _derive_examination_timestamp(rows: list[SapIshNormalizedRow]) -> datetime | None:
    timestamps = [
        timestamp for row in rows if (timestamp := _extract_row_timestamp(row))
    ]
    if not timestamps:
        return None
    return min(timestamps)


def _build_payload_for_case(
    *,
    primary_row: SapIshNormalizedRow,
    related_rows: list[SapIshNormalizedRow],
    source_system: str,
    center_name: str | None,
    center_key: str | None,
) -> tuple[SapIshDropFilePayload, str]:
    normalized_document = {
        "document_type": primary_row.document_type,
        "canonical_row": primary_row.canonical_row,
        "raw_columns": primary_row.raw_columns,
    }
    payload_data: dict[str, SapIshImportPayloadValue] = build_preanonymized_payload(
        normalized_document,
        source_system=source_system,
        center_name=center_name,
        center_key=center_key,
    )

    summary_text = _extract_preferred_text(primary_row) or _build_case_summary(
        related_rows
    )
    if summary_text:
        payload_data["anonymized_text"] = summary_text
        payload_data.setdefault("text", summary_text)

    if not payload_data.get("patient_gender"):
        patient_gender = _derive_patient_gender(related_rows)
        if patient_gender:
            payload_data["patient_gender"] = patient_gender

    if not payload_data.get("examination_date"):
        timestamp = _derive_examination_timestamp(related_rows)
        if timestamp is not None:
            payload_data["examination_date"] = timestamp.date().isoformat()
            payload_data["examination_time"] = (
                timestamp.time().replace(microsecond=0).isoformat()
            )

    payload_data["raw_columns"] = _build_enriched_raw_columns(
        primary_row=primary_row,
        related_rows=related_rows,
    )
    payload_data["source_document_type"] = primary_row.document_type
    return _validate_sap_ish_drop_file_payload(payload_data), summary_text


def _slugify_token(value: str | None, *, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    token = value.strip()
    allowed: list[str] = [
        char.lower() if char.isalnum() else (char if char in {"-", "_"} else "_")
        for char in token
    ]
    collapsed = "".join(allowed).strip("_")
    return collapsed or fallback


def _write_drop_file(
    *,
    output_dir: Path,
    case_index: int,
    primary_row: SapIshNormalizedRow,
    payload: SapIshDropFilePayload,
    carrier_text: str,
) -> GeneratedDropFile:
    ensure_directory(output_dir)
    payload_dict: SapIshImportPayload = dump_sap_ish_drop_file_payload(payload)
    external_id_raw = payload_dict.get("external_id")
    case_number_raw = payload_dict.get("casenumber")
    patient_token = _slugify_token(
        external_id_raw if isinstance(external_id_raw, str) else None,
        fallback="patient",
    )
    case_token = _slugify_token(
        case_number_raw if isinstance(case_number_raw, str) else None,
        fallback=f"case_{case_index:04d}",
    )
    document_token = _slugify_token(primary_row.document_type, fallback="document")
    stem = f"sap_ish_{case_index:04d}_{document_token}_{patient_token}_{case_token}"
    carrier_path = output_dir / f"{stem}.txt"
    sidecar_path = output_dir / f"{stem}.json"
    atomic_write_file(
        destination=carrier_path,
        content=[f"{carrier_text.strip()}\n".encode("utf-8")],
    )
    atomic_write_file(
        destination=sidecar_path,
        content=[
            json.dumps(
                payload_dict, ensure_ascii=True, indent=2, sort_keys=True
            ).encode("utf-8")
        ],
    )
    return GeneratedDropFile(
        carrier_path=carrier_path,
        sidecar_path=sidecar_path,
        payload=payload,
        document_type=primary_row.document_type,
    )


def convert_sap_ish_zip_to_preanonymized_drop(
    *,
    zip_path: Path | str,
    output_dir: Path | str,
    source_system: str = "sap_ish",
    center_name: str | None = None,
    center_key: str | None = None,
) -> SapIshImportResult:
    archive_path = Path(zip_path).expanduser().resolve()
    destination_dir = ensure_within_data_root(Path(output_dir).expanduser().resolve())

    if not archive_path.exists():
        raise FileNotFoundError(f"SAP IS-H zip not found: {archive_path}")

    with TemporaryDirectory(prefix="sap_ish_import_") as temp_dir_name:
        extract_dir = Path(temp_dir_name)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)

        normalized_rows, matched_files, skipped_files = _normalize_supported_rows(
            extract_dir
        )
        if not normalized_rows:
            raise ValueError(
                "No supported SAP IS-H TSV tables were found in the provided zip archive"
            )

        rows_by_case: dict[tuple[str, str], list[SapIshNormalizedRow]] = defaultdict(
            list
        )
        rows_by_patient: dict[str, list[SapIshNormalizedRow]] = defaultdict(list)
        patients_with_cases: set[str] = set()

        for row in normalized_rows:
            case_key = _extract_case_key(row)
            patient_key = _extract_patient_key(row)
            if case_key is not None:
                rows_by_case[case_key].append(row)
                patients_with_cases.add(case_key[0])
            elif patient_key is not None:
                rows_by_patient[patient_key].append(row)

        generated_files: list[GeneratedDropFile] = []
        case_counter = 0

        for case_key in sorted(rows_by_case):
            patient_key = case_key[0]
            related_rows = [
                *rows_by_case[case_key],
                *rows_by_patient.get(patient_key, []),
            ]
            primary_rows = [
                row
                for row in related_rows
                if row.document_type in TEXT_DOCUMENT_TYPES
                and _extract_preferred_text(row)
            ]
            if not primary_rows:
                primary_rows = [_pick_anchor_row(related_rows)]

            for primary_row in primary_rows:
                case_counter += 1
                payload, carrier_text = _build_payload_for_case(
                    primary_row=primary_row,
                    related_rows=related_rows,
                    source_system=source_system,
                    center_name=center_name,
                    center_key=center_key,
                )
                generated_files.append(
                    _write_drop_file(
                        output_dir=destination_dir,
                        case_index=case_counter,
                        primary_row=primary_row,
                        payload=payload,
                        carrier_text=carrier_text,
                    )
                )

        for patient_key in sorted(rows_by_patient):
            if patient_key in patients_with_cases:
                continue
            related_rows = rows_by_patient[patient_key]
            primary_row = _pick_anchor_row(related_rows)
            case_counter += 1
            payload, carrier_text = _build_payload_for_case(
                primary_row=primary_row,
                related_rows=related_rows,
                source_system=source_system,
                center_name=center_name,
                center_key=center_key,
            )
            generated_files.append(
                _write_drop_file(
                    output_dir=destination_dir,
                    case_index=case_counter,
                    primary_row=primary_row,
                    payload=payload,
                    carrier_text=carrier_text,
                )
            )

    return SapIshImportResult(
        generated_files=tuple(generated_files),
        matched_source_files=tuple(sorted(matched_files)),
        skipped_source_files=tuple(sorted(skipped_files)),
    )


__all__ = [
    "GeneratedDropFile",
    "SapIshImportResult",
    "SapIshNormalizedRow",
    "convert_sap_ish_zip_to_preanonymized_drop",
]
