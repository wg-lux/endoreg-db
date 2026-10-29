from __future__ import annotations

import pytest
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)
from pydantic import BaseModel, ConfigDict

from endoreg_db.schemas.persisted_json import (
    validate_dtypes_p_examination_payload,
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination


class _UnownedPersistencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient: str
    examination: str
    unowned_field: str


def test_shared_dtypes_record_model_is_canonicalized_for_json_persistence() -> None:
    shared_payload = DtypesRecordPersistencePayload(
        patient="patient-17",
        examination="colonoscopy",
        knowledge_base_module="gastroenterology_reporting",
        knowledge_base_version="2026.08",
    )

    persisted = validate_dtypes_p_examination_payload(shared_payload)

    assert persisted == shared_payload.model_dump(mode="json", exclude_none=True)
    assert persisted["patient_findings"] == []
    assert persisted["patient_indications"] == []


def test_patient_examination_model_boundary_accepts_shared_contract() -> None:
    shared_payload = DtypesRecordPersistencePayload(
        patient="patient-17",
        examination="colonoscopy",
    )
    patient_examination = PatientExamination(
        patient_id=17,
        dtypes_record=shared_payload,
    )

    patient_examination.clean()

    assert patient_examination.dtypes_record == shared_payload.model_dump(
        mode="json", exclude_none=True
    )


def test_arbitrary_pydantic_model_cannot_bypass_shared_persistence_contract() -> None:
    unowned_payload = _UnownedPersistencePayload(
        patient="patient-17",
        examination="colonoscopy",
        unowned_field="must-not-persist",
    )

    with pytest.raises(ValueError, match="unowned_field"):
        validate_dtypes_p_examination_payload(unowned_payload)


@pytest.mark.parametrize("invalid_value", [[], "payload", 17, True])
def test_non_object_values_fail_before_persistence(invalid_value: object) -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_dtypes_p_examination_payload(invalid_value)
