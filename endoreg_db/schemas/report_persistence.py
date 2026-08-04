from __future__ import annotations

from typing import Any, cast

from lx_dtypes.models.contracts.patient_examination_report import (
    ReportJsonObject,
)
from pydantic import ConfigDict, TypeAdapter


REPORT_JSON_CONTRACT_VERSION = "lx-dtypes==0.2.9"

_REPORT_JSON_OBJECT_ADAPTER = TypeAdapter(
    ReportJsonObject,
    config=ConfigDict(strict=True, allow_inf_nan=False),
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


__all__ = [
    "REPORT_JSON_CONTRACT_VERSION",
    "validate_persisted_report_json_object",
]
