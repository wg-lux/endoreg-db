from __future__ import annotations

from endoreg_db.schemas.lookup_state import (
    LookupState,
    build_lookup_recompute_response,
    normalize_lookup_keys,
    validate_lookup_state,
    validate_lookup_updates,
)


def test_lookup_state_is_local_endoreg_contract() -> None:
    assert LookupState.__module__ == "endoreg_db.schemas.lookup_state"


def test_validate_lookup_state_normalizes_legacy_keys() -> None:
    payload = {
        "patient_examination_id": 42,
        "selectedRequirementSetIds": [1, 2, 3],
        "selectedChoices": {"req_10": {"choice": "a"}},
    }

    normalized = validate_lookup_state(payload)
    assert normalized is not None
    assert normalized["selected_requirement_set_ids"] == [1, 2, 3]
    assert normalized["selected_choices"] == {"req_10": {"choice": "a"}}


def test_normalize_lookup_keys_maps_legacy_keys() -> None:
    normalized = normalize_lookup_keys(
        {
            "selectedRequirementSetIds": [4],
            "selectedChoices": {"req_11": {"choice": "b"}},
        }
    )

    assert normalized["selected_requirement_set_ids"] == [4]
    assert normalized["selected_choices"] == {"req_11": {"choice": "b"}}


def test_validate_lookup_updates_accepts_typed_updates_payload() -> None:
    updates = validate_lookup_updates(
        {
            "requirement_status": {"10": False},
            "candidate_requirement_set_ids": [20, 21],
            "candidate_requirement_set_confidence": 0.72,
        }
    )

    assert updates["requirement_status"] == {"10": False}
    assert updates["candidate_requirement_set_ids"] == [20, 21]
    assert updates["candidate_requirement_set_confidence"] == 0.72


def test_recompute_response_contract_shape() -> None:
    response = build_lookup_recompute_response(
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
