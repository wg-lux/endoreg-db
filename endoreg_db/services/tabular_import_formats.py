from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, cast

import yaml

from endoreg_db.data import TABULAR_IMPORT_TEMPLATES_FILE


def _normalize_header(value: str) -> str:
    return str(value).strip().casefold()


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _parse_bool(value: Any) -> bool | None:
    normalized = _normalize_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool):
        return normalized
    if isinstance(normalized, (int, float)):
        return bool(normalized)
    if isinstance(normalized, str):
        lowered = normalized.casefold()
        if lowered in {"1", "true", "yes", "y", "ja"}:
            return True
        if lowered in {"0", "false", "no", "n", "nein"}:
            return False
    return None


def _parse_datetime(value: Any) -> datetime | None:
    normalized = _normalize_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, datetime):
        return normalized
    if isinstance(normalized, date):
        return datetime.combine(normalized, time.min)
    if not isinstance(normalized, str):
        return None

    candidate = normalized.replace("T", " ")
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _parse_int(value: Any) -> int | None:
    normalized = _normalize_scalar(value)
    if normalized is None:
        return None
    if isinstance(normalized, bool):
        return int(normalized)
    if isinstance(normalized, int):
        return normalized
    if isinstance(normalized, float):
        return int(normalized)
    if isinstance(normalized, str):
        try:
            return int(normalized)
        except ValueError:
            return None
    return None


def _coerce_value(value: Any, declared_type: str) -> Any:
    if declared_type == "str":
        normalized = _normalize_scalar(value)
        return None if normalized is None else str(normalized)
    if declared_type == "datetime":
        return _parse_datetime(value)
    if declared_type == "int":
        return _parse_int(value)
    if declared_type == "bool":
        return _parse_bool(value)
    return value


def _yaml_mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping")
    rows = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in rows):
        raise ValueError(f"{field_name} keys must be strings")
    return cast(Mapping[str, object], rows)


def _yaml_mapping_sequence(
    value: object, *, field_name: str
) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    rows = cast(list[object], value)
    return tuple(
        _yaml_mapping(item, field_name=f"{field_name}[{index}]")
        for index, item in enumerate(rows)
    )


def _yaml_string_sequence(value: object, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    rows = cast(list[object], value)
    if not all(isinstance(item, str) for item in rows):
        raise ValueError(f"{field_name} entries must be strings")
    return tuple(cast(list[str], rows))


@dataclass(frozen=True, slots=True)
class ColumnTemplate:
    source_column: str
    target: str
    value_type: str


@dataclass(frozen=True, slots=True)
class DocumentTemplate:
    document_type: str
    description: str
    required_columns: tuple[str, ...]
    columns: tuple[ColumnTemplate, ...]

    @property
    def normalized_required_columns(self) -> tuple[str, ...]:
        return tuple(_normalize_header(column) for column in self.required_columns)

    @property
    def normalized_source_columns(self) -> tuple[str, ...]:
        return tuple(_normalize_header(column.source_column) for column in self.columns)


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    template: DocumentTemplate
    matched_columns: tuple[str, ...]
    missing_required_columns: tuple[str, ...]
    unknown_columns: tuple[str, ...]


def _load_template_file(path: Path) -> list[DocumentTemplate]:
    loaded_payload: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    payload = _yaml_mapping(loaded_payload, field_name="tabular import template file")
    template_rows = _yaml_mapping_sequence(
        payload.get("templates") or [],
        field_name="templates",
    )
    templates: list[DocumentTemplate] = []

    for template_row in template_rows:
        columns = _yaml_mapping(template_row.get("columns") or {}, field_name="columns")
        column_specs = tuple(
            ColumnTemplate(
                source_column=source_column,
                target=str(spec["target"]),
                value_type=str(spec.get("type", "str")),
            )
            for source_column, spec in (
                (
                    source_column,
                    _yaml_mapping(spec, field_name=f"columns.{source_column}"),
                )
                for source_column, spec in columns.items()
            )
        )
        templates.append(
            DocumentTemplate(
                document_type=str(template_row["document_type"]),
                description=str(template_row.get("description", "")),
                required_columns=_yaml_string_sequence(
                    template_row.get("required_columns"),
                    field_name=f"{template_row['document_type']}.required_columns",
                ),
                columns=column_specs,
            )
        )
    return templates


@lru_cache(maxsize=1)
def load_document_templates() -> tuple[DocumentTemplate, ...]:
    return tuple(_load_template_file(TABULAR_IMPORT_TEMPLATES_FILE))


def resolve_document_template(headers: list[str] | tuple[str, ...]) -> TemplateMatch:
    normalized_headers = {
        _normalize_header(header): str(header).strip() for header in headers
    }
    header_keys = set(normalized_headers)

    matches: list[TemplateMatch] = []
    for template in load_document_templates():
        required = set(template.normalized_required_columns)
        if not required.issubset(header_keys):
            continue

        template_columns = set(template.normalized_source_columns)
        matched = tuple(
            normalized_headers[key] for key in header_keys if key in template_columns
        )
        unknown = tuple(
            normalized_headers[key]
            for key in header_keys
            if key not in template_columns
        )
        matches.append(
            TemplateMatch(
                template=template,
                matched_columns=tuple(sorted(matched)),
                missing_required_columns=(),
                unknown_columns=tuple(sorted(unknown)),
            )
        )

    if not matches:
        raise ValueError(
            "No known tabular import template matches the provided headers"
        )

    matches.sort(
        key=lambda item: (
            len(item.matched_columns),
            -len(item.unknown_columns),
        ),
        reverse=True,
    )
    best = matches[0]
    tied = [
        match
        for match in matches
        if len(match.matched_columns) == len(best.matched_columns)
        and len(match.unknown_columns) == len(best.unknown_columns)
    ]
    if len(tied) > 1:
        template_names = ", ".join(match.template.document_type for match in tied)
        raise ValueError(f"Ambiguous tabular import headers; matched: {template_names}")
    return best


def normalize_document_row(
    row: dict[str, Any],
    *,
    template_match: TemplateMatch | None = None,
) -> dict[str, Any]:
    template_match = template_match or resolve_document_template(tuple(row.keys()))
    source_lookup = {_normalize_header(key): key for key in row}
    template_columns = set(template_match.template.normalized_source_columns)
    canonical_row: dict[str, Any] = {}
    raw_columns: dict[str, Any] = {}

    for column_template in template_match.template.columns:
        normalized_source = _normalize_header(column_template.source_column)
        original_key = source_lookup.get(normalized_source)
        if original_key is None:
            continue
        raw_value = row.get(original_key)
        raw_columns[original_key] = raw_value
        canonical_row[column_template.target] = _coerce_value(
            raw_value,
            column_template.value_type,
        )

    unknown_columns = {
        key: row[key] for key in row if _normalize_header(key) not in template_columns
    }
    return {
        "document_type": template_match.template.document_type,
        "description": template_match.template.description,
        "canonical_row": canonical_row,
        "raw_columns": raw_columns,
        "unknown_columns": unknown_columns,
        "matched_columns": list(template_match.matched_columns),
    }


def build_preanonymized_payload(
    normalized_document: dict[str, Any],
    *,
    source_system: str,
    center_name: str | None = None,
    center_key: str | None = None,
) -> dict[str, Any]:
    canonical_row = dict(normalized_document.get("canonical_row") or {})
    timestamp = next(
        (
            canonical_row.get(field_name)
            for field_name in (
                "dokumentzeit",
                "date_erstellzeit",
                "zugangszeit",
                "diagnosezeit",
                "beginnzeit",
                "apply_date",
                "prepare_date",
                "creation_date",
            )
            if canonical_row.get(field_name) is not None
        ),
        None,
    )

    anonymized_text = next(
        (
            canonical_row.get(field_name)
            for field_name in (
                "pmd_anam",
                "str_text",
                "befund",
                "kurbefund",
                "beurteilung",
            )
            if canonical_row.get(field_name)
        ),
        None,
    )

    payload: dict[str, Any] = {
        "external_id": canonical_row.get("patient_nr")
        or canonical_row.get("source_patient_id"),
        "external_id_origin": source_system,
        "casenumber": canonical_row.get("fall_nr"),
        "anonymized_text": anonymized_text,
        "patient_gender": canonical_row.get("geschlecht"),
        "source_system": source_system,
        "source_document_type": normalized_document.get("document_type"),
        "original_document_id": canonical_row.get("dokumentnummer")
        or canonical_row.get("main_order_id")
        or canonical_row.get("medication_row_id"),
        "original_document_version": canonical_row.get("dokumentversion"),
        "raw_columns": normalized_document.get("raw_columns") or {},
    }
    if center_name:
        payload["center_name"] = center_name
    if center_key:
        payload["center_key"] = center_key
    if isinstance(timestamp, datetime):
        payload["examination_date"] = timestamp.date().isoformat()
        payload["examination_time"] = (
            timestamp.time().replace(microsecond=0).isoformat()
        )
    return {key: value for key, value in payload.items() if value not in (None, "", {})}


__all__ = [
    "ColumnTemplate",
    "DocumentTemplate",
    "TemplateMatch",
    "build_preanonymized_payload",
    "load_document_templates",
    "normalize_document_row",
    "resolve_document_template",
]
