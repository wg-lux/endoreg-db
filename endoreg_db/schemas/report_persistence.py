from __future__ import annotations

from typing import Any, Literal, cast

from lx_dtypes.models.contracts.patient_examination_report import (
    ReportJsonObject,
)
from pydantic import ConfigDict, TypeAdapter


REPORT_JSON_CONTRACT_VERSION = "lx-dtypes==0.2.9"
ReportLanguage = Literal["de", "en"]

_REPORT_JSON_OBJECT_ADAPTER = TypeAdapter(
    ReportJsonObject,
    config=ConfigDict(strict=True, allow_inf_nan=False),
)
_REPORT_LANGUAGE_ADAPTER: TypeAdapter[ReportLanguage] = TypeAdapter(
    Literal["de", "en"],
    config=ConfigDict(strict=True),
)


def validate_persisted_report_json_object(
    value: Any,
    *,
    field_name: str,
) -> ReportJsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    validated = _REPORT_JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    return cast(
        ReportJsonObject,
        _REPORT_JSON_OBJECT_ADAPTER.dump_python(validated, mode="json"),
    )


def validate_report_editor_payload(
    value: Any,
    *,
    default_language: ReportLanguage | None = None,
) -> ReportJsonObject:
    """Canonicalize report editor JSON and validate its language provenance."""
    payload = validate_persisted_report_json_object(
        value,
        field_name="editor_payload",
    )
    snake_language = payload.get("report_language")
    camel_language = payload.get("reportLanguage")
    if (
        snake_language is not None
        and camel_language is not None
        and snake_language != camel_language
    ):
        raise ValueError("editor_payload report language aliases disagree")
    language_value = (
        snake_language
        if snake_language is not None
        else camel_language
        if camel_language is not None
        else default_language
    )
    if language_value is None:
        return payload
    language = _REPORT_LANGUAGE_ADAPTER.validate_python(language_value, strict=True)
    payload.pop("reportLanguage", None)
    payload["report_language"] = language
    return payload


def report_language_from_editor_payload(value: object) -> ReportLanguage:
    """Return the validated report language, defaulting legacy reports to German."""
    payload = validate_report_editor_payload(value, default_language="de")
    return _REPORT_LANGUAGE_ADAPTER.validate_python(
        payload["report_language"],
        strict=True,
    )


__all__ = [
    "REPORT_JSON_CONTRACT_VERSION",
    "ReportLanguage",
    "report_language_from_editor_payload",
    "validate_persisted_report_json_object",
    "validate_report_editor_payload",
]
