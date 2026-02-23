from __future__ import annotations
from endoreg_db.schemas.lookup_state import (
    LookupState as AdapterLookupState,
    build_lookup_recompute_response as adapter_build_lookup_recompute_response,
    normalize_lookup_keys as adapter_normalize_lookup_keys,
    validate_lookup_state as adapter_validate_lookup_state,
    validate_lookup_updates as adapter_validate_lookup_updates,
)
from lx_dtypes.models.knowledge_base.report_template import (
    LookupState as DtypesLookupState,
    build_lookup_recompute_response as dtypes_build_lookup_recompute_response,
    normalize_lookup_keys as dtypes_normalize_lookup_keys,
    validate_lookup_state as dtypes_validate_lookup_state,
    validate_lookup_updates as dtypes_validate_lookup_updates,
)


def test_adapter_re_exports_same_lookup_state_class() -> None:
    assert AdapterLookupState is DtypesLookupState


def test_adapter_re_exports_same_helper_functions() -> None:
    assert adapter_normalize_lookup_keys is dtypes_normalize_lookup_keys
    assert adapter_validate_lookup_state is dtypes_validate_lookup_state
    assert adapter_validate_lookup_updates is dtypes_validate_lookup_updates
    assert adapter_build_lookup_recompute_response is dtypes_build_lookup_recompute_response


def test_validate_lookup_state_normalizes_legacy_keys() -> None:
    payload = {
        "patient_examination_id": 42,
        "selectedRequirementSetIds": [1, 2, 3],
        "selectedChoices": {"req_10": {"choice": "a"}},
    }

    normalized = adapter_validate_lookup_state(payload)
    assert normalized is not None
    assert normalized["selected_requirement_set_ids"] == [1, 2, 3]
    assert normalized["selected_choices"] == {"req_10": {"choice": "a"}}


def test_recompute_response_contract_shape() -> None:
    response = adapter_build_lookup_recompute_response(
        token="abc123",
        updates={
            "requirements_by_set": {},
            "requirement_status": {"10": False},
            "requirement_set_status": {"20": False},
            "requirement_defaults": {},
            "classification_choices": {},
            "suggested_actions": {"10": [{"type": "add_finding", "finding_id": 3}]},
            "candidate_requirement_set_ids": [20, 21],
            "candidate_requirement_set_confidence": 0.72,
        },
    )

    assert response["ok"] is True
    assert response["token"] == "abc123"
    assert "updates" in response
    assert response["updates"]["requirement_status"] == {"10": False}
    assert response["updates"]["candidate_requirement_set_ids"] == [20, 21]
    assert response["updates"]["candidate_requirement_set_confidence"] == 0.72
