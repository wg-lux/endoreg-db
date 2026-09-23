from __future__ import annotations

from typing import cast

import pytest
from lx_dtypes.models.contracts.json_types import JsonValue

from endoreg_db.models import Examination, Patient, PatientExamination
from endoreg_db.services.dtypes_records import (
    persist_patient_examination_dtypes_record,
)


def _payload_with_nested_reference(
    *,
    nested_field: str,
    nested_reference: str,
) -> dict[str, JsonValue]:
    nested_item: dict[str, JsonValue]
    if nested_field == "patient_findings":
        nested_item = {
            "finding": "colon_polyp",
            "patient_examination": nested_reference,
        }
    else:
        nested_item = {
            "indication": "screening_colonoscopy",
            "patient_examination": nested_reference,
        }
    return {
        "patient": "1",
        "examination": "colonoscopy",
        nested_field: [cast(JsonValue, nested_item)],
    }


@pytest.mark.django_db
@pytest.mark.parametrize("nested_field", ["patient_findings", "patient_indications"])
def test_persistence_rejects_conflicting_nested_examination_without_rewriting(
    nested_field: str,
) -> None:
    patient = Patient.objects.create(
        pk=1,
        patient_hash=f"dtypes-conflict-{nested_field}",
    )
    examination, _created = Examination.objects.get_or_create(name="colonoscopy")
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        examination=examination,
        dtypes_record={"patient": "previous", "examination": "colonoscopy"},
    )
    patient_examination.refresh_from_db()
    original_record = patient_examination.dtypes_record

    with pytest.raises(ValueError, match="same patient_examination"):
        persist_patient_examination_dtypes_record(
            patient_examination,
            _payload_with_nested_reference(
                nested_field=nested_field,
                nested_reference="gastroscopy",
            ),
        )

    patient_examination.refresh_from_db()
    assert patient_examination.dtypes_record == original_record
