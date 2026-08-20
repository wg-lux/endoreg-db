from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import os
from typing import Any, Protocol, cast

from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    parse_dtypes_record_persistence_payload,
)
from lx_dtypes.models.contracts.json_types import JsonValue
from pydantic import BaseModel


class _ExaminationLike(Protocol):
    name: str


class _PatientExaminationLike(Protocol):
    id: int | None
    examination_safe: _ExaminationLike
    dtypes_record: dict[str, Any]
    dtypes_record_updated_at: datetime | None
    knowledge_base_module: str
    knowledge_base_version: str

    def save(self, *args: object, **kwargs: object) -> None: ...


class _DtypesFindingLike(Protocol):
    patient_examination: str


class _DtypesIndicationLike(Protocol):
    patient_examination: str


class _DtypesRecordLike(Protocol):
    examination: str
    patient: str
    knowledge_base_module: str | None
    knowledge_base_version: str | None
    patient_findings: list[_DtypesFindingLike]
    patient_indications: list[_DtypesIndicationLike]

    def model_dump(self, *args: object, **kwargs: object) -> dict[str, Any]: ...


def _patient_examination(value: object) -> _PatientExaminationLike:
    if not hasattr(value, "examination_safe"):
        raise ValueError("patient_examination must expose examination_safe")
    if not hasattr(value, "save"):
        raise ValueError("patient_examination must expose save()")
    return cast(_PatientExaminationLike, value)


def _validated_p_examination(
    value: BaseModel | Mapping[str, JsonValue],
) -> _DtypesRecordLike:
    if isinstance(value, BaseModel):
        candidate = cast(
            dict[str, JsonValue], value.model_dump(mode="json", exclude_none=True)
        )
    else:
        candidate = dict(value)
    if not candidate:
        raise ValueError("dtypes_record must not be empty")
    kb_module = candidate.get("knowledge_base_module")
    kb_version = candidate.get("knowledge_base_version")

    candidate["knowledge_base_module"] = kb_module if kb_module else None
    candidate["knowledge_base_version"] = kb_version if kb_version else None

    return cast(_DtypesRecordLike, parse_dtypes_record_persistence_payload(candidate))


def _validate_host_patient_examination_match(
    patient_examination: _PatientExaminationLike,
    payload: _DtypesRecordLike,
) -> None:
    examination = patient_examination.examination_safe
    if payload.examination != examination.name:
        raise ValueError(
            "dtypes_record examination does not match PatientExamination: "
            f"'{payload.examination}' != '{examination.name}'"
        )

    expected_patient_examination = examination.name
    patient = str(getattr(patient_examination, "patient_id", ""))
    if payload.patient != patient:
        raise ValueError(
            "dtypes_record patient does not match PatientExamination: "
            f"'{payload.patient}' != '{patient}'"
        )

    for patient_finding in payload.patient_findings:
        if patient_finding.patient_examination != expected_patient_examination:
            raise ValueError(
                "dtypes_record patient finding belongs to another "
                "PatientExamination: "
                f"'{patient_finding.patient_examination}' != "
                f"'{expected_patient_examination}'"
            )
    for patient_indication in payload.patient_indications:
        if patient_indication.patient_examination != expected_patient_examination:
            raise ValueError(
                "dtypes_record patient indication belongs to another "
                "PatientExamination: "
                f"'{patient_indication.patient_examination}' != "
                f"'{expected_patient_examination}'"
            )


def persist_patient_examination_dtypes_record(
    patient_examination: object,
    value: BaseModel | Mapping[str, JsonValue],
) -> dict[str, Any]:
    host_patient_examination = _patient_examination(patient_examination)
    payload = _validated_p_examination(value)
    _validate_host_patient_examination_match(host_patient_examination, payload)

    record = payload.model_dump(mode="json", exclude_none=True)
    update_fields = ["dtypes_record", "dtypes_record_updated_at"]
    host_patient_examination.dtypes_record = record
    host_patient_examination.dtypes_record_updated_at = timezone.now()

    if payload.knowledge_base_module:
        host_patient_examination.knowledge_base_module = payload.knowledge_base_module
        update_fields.append("knowledge_base_module")
    if payload.knowledge_base_version:
        host_patient_examination.knowledge_base_version = payload.knowledge_base_version
        update_fields.append("knowledge_base_version")

    with transaction.atomic():
        host_patient_examination.save(update_fields=update_fields)

    return record


def persist_patient_examination_dtypes_record_from_ledger(
    patient_examination: object,
    *,
    route_module_name: str | None = None,
) -> dict[str, Any]:
    from endoreg_db.integrations import lx_dtypes_host_models
    from lx_dtypes.django.api.findings_routes import (
        build_p_examination_payload_from_host_ledger,
    )

    host_patient_examination = _patient_examination(patient_examination)
    module_name = (
        route_module_name
        or host_patient_examination.knowledge_base_module
        or os.getenv("LX_DTYPES_FINDINGS_MODULE", "lx_knowledge_base")
    )
    payload = build_p_examination_payload_from_host_ledger(
        patient_examination,
        route_module_name=module_name,
        orm_models=lambda: dict(lx_dtypes_host_models.orm_models()),
        active_patient_findings_queryset=(
            lambda: lx_dtypes_host_models.active_patient_findings_queryset()
        ),
    )
    return persist_patient_examination_dtypes_record(patient_examination, payload)


__all__ = [
    "persist_patient_examination_dtypes_record",
    "persist_patient_examination_dtypes_record_from_ledger",
]
