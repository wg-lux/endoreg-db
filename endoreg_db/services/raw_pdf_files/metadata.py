from __future__ import annotations

import copy
import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol, cast

from django.core.exceptions import ValidationError
from lx_dtypes.models.contracts.json_types import JsonNull, JsonValue
from pydantic import BaseModel, ConfigDict, Field, field_validator

from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.utils.storage import ensure_local_file, file_exists

if TYPE_CHECKING:
    from collections.abc import Iterable

    from endoreg_db.models.administration.center.center import Center as CenterModel
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )


type ReportMetaJsonValue = (
    JsonValue
    | JsonNull
    | list["ReportMetaJsonValue"]
    | dict[str, "ReportMetaJsonValue"]
)
type ReportMetaJsonObject = dict[str, ReportMetaJsonValue]


class _ReportSensitiveMeta(Protocol):
    pseudo_patient: "Patient | None"
    pseudo_examination: "PatientExamination | None"
    center: "CenterModel | None"

    def update_from_dict(self, data: ReportMetaJsonObject) -> None: ...

    def save(self) -> None: ...


class _ReportReaderFlag(Protocol):
    value: str


class _ReportReaderFlagRows(Protocol):
    def all(self) -> "Iterable[_ReportReaderFlag]": ...


class _ReportReaderPdfType(Protocol):
    patient_info_line: _ReportReaderFlag
    endoscope_info_line: _ReportReaderFlag | None
    examiner_info_line: _ReportReaderFlag
    cut_off_below_lines: _ReportReaderFlagRows
    cut_off_above_lines: _ReportReaderFlagRows


logger = logging.getLogger(__name__)


class ReportProcessingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    anonymized_text: str | None = None
    raw_meta: ReportMetaJsonObject = Field(default_factory=dict)

    @field_validator("raw_meta", mode="before")
    @classmethod
    def _validate_raw_meta(cls, value: object) -> ReportMetaJsonObject:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("raw_meta must be a dictionary")
        return _json_compatible_mapping(cast(dict[object, object], value))


def _json_compatible_value(value: object) -> ReportMetaJsonValue:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_compatible_value(item) for item in cast(list[object], value)]
    if isinstance(value, tuple):
        return [
            _json_compatible_value(item) for item in cast(tuple[object, ...], value)
        ]
    if isinstance(value, dict):
        return _json_compatible_mapping(cast(dict[object, object], value))
    raise TypeError(f"Unsupported raw_meta value type: {type(value).__name__}")


def _json_compatible_mapping(value: dict[object, object]) -> ReportMetaJsonObject:
    payload: ReportMetaJsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("raw_meta keys must be strings")
        payload[key] = _json_compatible_value(item)
    return payload


def prepare_raw_pdf_before_save(report: "RawPdfFile") -> None:
    if not report.pk and not report.pdf_hash and report.file:
        try:
            with ensure_local_file(report.file) as local_path:
                report.pdf_hash = get_pdf_hash(local_path)
                logger.info("Calculated hash during pre-save for %s", report.file.name)
        except Exception as exc:
            logger.warning(
                "Could not calculate hash before initial save for %s: %s",
                report.file.name,
                exc,
            )

    file_name = report.file.name if report.file else None
    if file_name and not file_name.endswith(".pdf"):
        raise ValidationError("Only report files are allowed")

    if not report.pdf_hash and report.pk and report.file and file_exists(report.file):
        try:
            with ensure_local_file(report.file) as local_path:
                logger.warning(
                    "Hash missing for saved file %s. Recalculating.",
                    report.file.name,
                )
                report.pdf_hash = get_pdf_hash(local_path)
        except Exception as exc:
            logger.error(
                "Could not calculate hash during save for existing file %s: %s",
                report.file.name,
                exc,
            )

    if not report.patient and report.sensitive_meta:
        sensitive_meta = cast(_ReportSensitiveMeta, report.sensitive_meta)
        report.patient = sensitive_meta.pseudo_patient
    if not report.examination and report.sensitive_meta:
        sensitive_meta = cast(_ReportSensitiveMeta, report.sensitive_meta)
        report.examination = sensitive_meta.pseudo_examination
    if not report.center and report.sensitive_meta:
        sensitive_meta = cast(_ReportSensitiveMeta, report.sensitive_meta)
        report.center = sensitive_meta.center


def process_raw_pdf_file(
    report: "RawPdfFile",
    text: str,
    anonymized_text: str,
    report_meta: ReportMetaJsonObject,
    verbose: bool,
) -> tuple[str, str, ReportMetaJsonObject]:
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

    report.text = text
    report.anonymized_text = anonymized_text

    assert report.center is not None, "Center must be set before processing file"

    report_meta["center_name"] = report.center.name
    if not report.sensitive_meta:
        sensitive_meta = SensitiveMeta.create_from_dict(report_meta)
        report.sensitive_meta = sensitive_meta
    else:
        sensitive_meta = report.sensitive_meta
        cast(_ReportSensitiveMeta, sensitive_meta).update_from_dict(report_meta)

    serializable_report_meta = copy.deepcopy(report_meta)
    payload = ReportProcessingPayload(
        text=text,
        anonymized_text=anonymized_text,
        raw_meta=serializable_report_meta,
    )
    report.raw_meta = payload.raw_meta

    cast(_ReportSensitiveMeta, sensitive_meta).save()
    report.save()

    return text, anonymized_text, report_meta


def build_report_reader_config(report: "RawPdfFile") -> ReportMetaJsonObject:
    from warnings import warn

    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.metadata.pdf_meta import PdfType

    center_obj = report.center
    assert center_obj is not None, "Center must be set to get report reader config"

    if not report.pdf_type:
        warn("PdfType not set, using default settings")
        pdf_type = PdfType.default_pdf_type()
    else:
        pdf_type = report.pdf_type
    center: Center = center_obj
    reader_pdf_type = cast(_ReportReaderPdfType, pdf_type)
    if reader_pdf_type.endoscope_info_line:
        endoscope_info_line = reader_pdf_type.endoscope_info_line.value
    else:
        endoscope_info_line = None

    return {
        "locale": "de_DE",
        "employee_first_names": [item.name for item in center.first_names.all()],
        "employee_last_names": [item.name for item in center.last_names.all()],
        "text_date_format": "%d.%m.%Y",
        "flags": {
            "patient_info_line": reader_pdf_type.patient_info_line.value,
            "endoscope_info_line": endoscope_info_line,
            "examiner_info_line": reader_pdf_type.examiner_info_line.value,
            "cut_off_below": [
                item.value for item in reader_pdf_type.cut_off_below_lines.all()
            ],
            "cut_off_above": [
                item.value for item in reader_pdf_type.cut_off_above_lines.all()
            ],
        },
    }
