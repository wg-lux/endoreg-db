from __future__ import annotations

from datetime import date, datetime, time

import pytest
from pydantic import ValidationError

from endoreg_db.schemas import (
    LocalStudyServerPreanonymizedIngestPayload,
    PreanonymizedIngestPayload,
)


def _local_study_server_payload() -> dict[str, object]:
    return {
        "center_key": "center-1",
        "source_system": "lx-annotate",
        "file_sha256": "a" * 64,
        "human_anonymization_validated": True,
        "validated_by": "operator-1",
        "validated_at": "2026-05-06T12:00:00+02:00",
        "examination_date": "2026-05-06",
    }


def test_preanonymized_payload_parses_dates_and_normalizes_blank_strings() -> None:
    payload = PreanonymizedIngestPayload.model_validate(
        {
            "external_id": " ext-42 ",
            "external_id_origin": " hospital ",
            "patient_first_name": " Alice ",
            "patient_last_name": " Miller ",
            "patient_dob": "1980-01-01",
            "examination_date": "2024-05-17",
            "examination_time": "09:30:00",
            "anonymized_text": "  ",
        }
    )

    assert payload.external_id == "ext-42"
    assert payload.external_id_origin == "hospital"
    assert payload.patient_first_name == "Alice"
    assert payload.patient_last_name == "Miller"
    assert payload.patient_dob == date(1980, 1, 1)
    assert payload.examination_date == date(2024, 5, 17)
    assert payload.examination_time == time(9, 30)
    assert payload.anonymized_text is None


def test_preanonymized_payload_json_dump_serializes_date_and_time_values() -> None:
    payload = PreanonymizedIngestPayload.model_validate(
        {
            "patient_dob": "1980-01-01",
            "examination_date": "2024-05-17",
            "examination_time": "09:30:00",
        }
    )

    assert payload.model_dump(exclude_none=True) == {
        "patient_dob": date(1980, 1, 1),
        "examination_date": date(2024, 5, 17),
        "examination_time": time(9, 30),
    }
    assert payload.model_dump(mode="json", exclude_none=True) == {
        "patient_dob": "1980-01-01",
        "examination_date": "2024-05-17",
        "examination_time": "09:30:00",
    }


def test_preanonymized_payload_accepts_canonical_identity_hashes() -> None:
    payload = PreanonymizedIngestPayload.model_validate(
        {
            "patient_hash": "a" * 64,
            "examination_hash": "0123456789abcdef" * 4,
        }
    )

    assert payload.patient_hash == "a" * 64
    assert payload.examination_hash == "0123456789abcdef" * 4


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"unexpected_field": "value"},
        {"external_id": "ext-42"},
        {"external_id_origin": "hospital"},
        {"external_id": " ", "external_id_origin": "hospital"},
        {"patient_hash": "a" * 63},
        {"patient_hash": "A" * 64},
        {"patient_hash": f" {'a' * 64}"},
        {"patient_hash": 1},
        {"examination_hash": "g" * 64},
    ],
)
def test_preanonymized_payload_rejects_noncanonical_input(
    invalid_payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PreanonymizedIngestPayload.model_validate(invalid_payload)


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"unexpected_confirmation": True},
        {"human_anonymization_validated": "true"},
    ],
)
def test_local_study_server_payload_rejects_unknown_and_coerced_input(
    invalid_update: dict[str, object],
) -> None:
    raw_payload = _local_study_server_payload()
    raw_payload.update(invalid_update)

    with pytest.raises(ValidationError):
        LocalStudyServerPreanonymizedIngestPayload.model_validate(raw_payload)


def test_local_study_server_payload_roundtrips_canonical_json() -> None:
    payload = LocalStudyServerPreanonymizedIngestPayload.model_validate(
        _local_study_server_payload()
    )

    canonical = payload.model_dump(mode="json", exclude_none=True)
    restored = LocalStudyServerPreanonymizedIngestPayload.model_validate(canonical)

    assert canonical == {
        "examination_date": "2026-05-06",
        "center_key": "center-1",
        "source_system": "lx-annotate",
        "file_sha256": "a" * 64,
        "human_anonymization_validated": True,
        "validated_by": "operator-1",
        "validated_at": "2026-05-06T12:00:00+02:00",
    }
    assert restored == payload
    assert restored.validated_at == datetime.fromisoformat("2026-05-06T12:00:00+02:00")
