from __future__ import annotations

from datetime import date, time

from endoreg_db.services.hub.payloads import PreanonymizedIngestPayload


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
