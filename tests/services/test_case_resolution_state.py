from __future__ import annotations

from typing import cast

import pytest

from lx_dtypes.models.contracts import CaseResolutionRequest

from endoreg_db.models import Center, RawPdfFile
from endoreg_db.services.case_resolution_state import (
    CASE_RESOLUTION_META_KEY,
    persist_case_resolution_state,
)


@pytest.mark.django_db
def test_persist_case_resolution_state_uses_contract_request() -> None:
    center = Center.objects.create(name="case-resolution-center")
    pdf = RawPdfFile.objects.create(center=center)
    payload = CaseResolutionRequest.model_validate(
        {
            "action": "attach",
            "patient_examination_id": 42,
        }
    )

    persist_case_resolution_state(
        media_obj=pdf,
        payload=payload,
        patient_examination_id=42,
        patient_id=7,
    )

    pdf.refresh_from_db()
    assert isinstance(pdf.raw_meta, dict)
    raw_meta = cast(dict[str, object], getattr(pdf, "raw_meta"))
    case_resolution_meta = cast(
        dict[str, object],
        raw_meta[CASE_RESOLUTION_META_KEY],
    )
    assert case_resolution_meta["last_action"] == "attach"
    assert case_resolution_meta["is_explicitly_resolved"] is True
    assert case_resolution_meta["linked_patient_examination_id"] == 42
    assert case_resolution_meta["linked_patient_id"] == 7
    assert case_resolution_meta["deferred"] is False
