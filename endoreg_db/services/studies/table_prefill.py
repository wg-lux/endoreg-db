"""Default column suggestions for the table mapping assistant, from headers and sampled cells.

Suggestions are column names and rule-set spellings only; sampled cell values
are used in memory and never returned. The saved mapping is validated as usual.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from functools import cache
from importlib.resources import files
from typing import Literal

from lx_dtypes.utils.study_setup_yaml import parse_setup_yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

PrefillField = Literal[
    "row_id",
    "first_name",
    "last_name",
    "dob",
    "gender",
    "examination_date",
    "examination",
    "finding",
]
PatientLineOutput = Literal["first_name", "last_name", "dob"]
PatientLineDetection = Literal["found", "not_found", "not_needed", "unavailable"]
DATE_FIELDS: frozenset[PrefillField] = frozenset({"dob", "examination_date"})
MAX_DATE_SAMPLES = 20


class PatientLineRules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    extractor: Literal["spacy_patient"]
    sample_cells: int = Field(ge=1, le=20)
    min_recognized: int = Field(ge=1, le=20)
    min_cell_chars: int = Field(ge=1, le=1000)
    max_columns: int = Field(ge=1, le=50)
    outputs: list[PatientLineOutput] = Field(min_length=1)


class TablePrefillRules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["1.0"]
    fields: dict[PrefillField, list[str]]
    gender_values: dict[str, list[str]]
    date_formats: list[str]
    patient_line: PatientLineRules


class FieldSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: PrefillField
    column: str
    extractor: Literal["spacy_patient"] | None = None
    output: PatientLineOutput | None = None
    date_format: str | None = None


class GenderSpelling(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    spelling: str
    identifier: str


class TablePrefill(BaseModel):
    """Lists rather than mappings: field and column names survive client key casing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: list[FieldSuggestion]
    gender_spellings: list[GenderSpelling]
    patient_line_column: str | None
    patient_line_detection: PatientLineDetection


@cache
def default_prefill_rules() -> TablePrefillRules:
    raw = (
        files("endoreg_db")
        .joinpath("data/study_import/prefill.yml")
        .read_text(encoding="utf-8")
    )
    return TablePrefillRules.model_validate(parse_setup_yaml(raw))


def _normalized(name: str) -> str:
    return re.sub(r"[\s_\-]+", " ", name.casefold()).strip(" .:")


def _header_matches(header: str, synonyms: set[str]) -> bool:
    normalized = _normalized(header)
    return normalized in synonyms or _normalized(header.rsplit(".", 1)[-1]) in synonyms


def _date_format(samples: Sequence[str], formats: Sequence[str]) -> str | None:
    """None when samples are ISO (the importer's native form) or no format fits all."""
    values = [value for value in samples[:MAX_DATE_SAMPLES] if value]
    if not values:
        return None
    try:
        for value in values:
            date.fromisoformat(value[:10])
        return None
    except ValueError:
        pass
    for candidate in formats:
        try:
            for value in values:
                datetime.strptime(value, candidate)
        except ValueError:
            continue
        return candidate
    return None


PatientExtractor = Callable[[str], Mapping[str, object]]
# The extractor reports unrecognized names as "unknown"; TableRecord rejects the same set.
_MISSING_NAMES = frozenset({"", "unknown", "unbekannt", "none", "null", "n/a"})


def _recognized(extracted: Mapping[str, object]) -> bool:
    return all(
        isinstance(value := extracted.get(output), str)
        and value.strip().casefold() not in _MISSING_NAMES
        for output in ("first_name", "last_name")
    )


def _patient_line_column(
    header: Sequence[str],
    samples: Mapping[str, Sequence[str]],
    taken: set[str],
    rules: PatientLineRules,
    extractor: PatientExtractor,
) -> str | None:
    candidates = [
        name
        for name in header
        if name not in taken
        and sum(
            len(value) >= rules.min_cell_chars and " " in value
            for value in samples[name]
        )
        >= rules.min_recognized
    ][: rules.max_columns]
    best: tuple[int, str] | None = None
    for name in candidates:
        cells = [
            value for value in samples[name] if len(value) >= rules.min_cell_chars
        ][: rules.sample_cells]
        recognized = 0
        for cell in cells:
            if _recognized(extractor(cell)):
                recognized += 1
        if recognized >= rules.min_recognized and (
            best is None or recognized > best[0]
        ):
            best = (recognized, name)
    return best[1] if best else None


def suggest_mapping(
    header: Sequence[str],
    samples: Mapping[str, Sequence[str]],
    rules: TablePrefillRules,
    *,
    extractor: PatientExtractor | None,
) -> TablePrefill:
    """Match headers to fields, detect date formats and, if needed, a patient line."""
    suggestions: dict[PrefillField, FieldSuggestion] = {}
    taken: set[str] = set()
    for field, synonyms in rules.fields.items():
        # Earlier synonyms win, so "Nachname" beats a generic "Name" column.
        match = next(
            (
                name
                for synonym in synonyms
                for name in header
                if name not in taken and _header_matches(name, {_normalized(synonym)})
            ),
            None,
        )
        if match is None:
            continue
        taken.add(match)
        suggestions[field] = FieldSuggestion(
            field=field,
            column=match,
            date_format=_date_format(samples[match], rules.date_formats)
            if field in DATE_FIELDS
            else None,
        )
    line_rules = rules.patient_line
    missing = [output for output in line_rules.outputs if output not in suggestions]
    # A generic "Name" header may hold the whole patient line, so name columns stay candidates.
    name_columns = {
        suggestions[field].column
        for field in ("first_name", "last_name")
        if field in suggestions
    }
    detection: PatientLineDetection = "not_needed"
    line_column: str | None = None
    if {"first_name", "last_name"} & set(missing):
        if extractor is None:
            detection = "unavailable"
        else:
            try:
                line_column = _patient_line_column(
                    header, samples, taken - name_columns, line_rules, extractor
                )
            except (ImportError, OSError, RuntimeError, ValueError):
                # The local spaCy model may be absent; header suggestions remain useful.
                logger.warning("Patient-line detection unavailable for table prefill")
                detection = "unavailable"
            else:
                detection = "found" if line_column else "not_found"
    if line_column is not None:
        for output in line_rules.outputs:
            if output in suggestions and suggestions[output].column != line_column:
                continue
            suggestions[output] = FieldSuggestion(
                field=output,
                column=line_column,
                extractor=line_rules.extractor,
                output=output,
            )
    return TablePrefill(
        fields=list(suggestions.values()),
        gender_spellings=[
            GenderSpelling(spelling=spelling, identifier=identifier)
            for identifier, spellings in rules.gender_values.items()
            for spelling in spellings
        ],
        patient_line_column=line_column,
        patient_line_detection=detection,
    )
