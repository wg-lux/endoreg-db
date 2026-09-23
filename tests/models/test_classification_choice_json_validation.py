from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models.medical.disease import Disease
from endoreg_db.models.medical.event import EventClassificationChoice
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndicationClassificationChoice,
)
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassificationChoice,
)
from endoreg_db.schemas.classification_choice import (
    build_patient_finding_numerical_descriptors,
    build_patient_finding_subcategories,
    validate_classification_choice_json,
)


VALID_SUBCATEGORIES = {
    "extent": {
        "choices": ["focal", "diffuse"],
        "default": "focal",
        "probability": [0.75, 0.25],
        "required": True,
        "description": "Distribution of the finding",
    }
}
VALID_NUMERICAL_DESCRIPTORS = {
    "size": {
        "unit": "mm",
        "required": False,
        "min": 0.0,
        "max": 100.0,
        "mean": None,
        "std": None,
        "default": None,
        "distribution": "uniform",
    }
}


@pytest.mark.parametrize(
    "model_cls",
    [
        Disease,
        EventClassificationChoice,
        ExaminationIndicationClassificationChoice,
        FindingClassificationChoice,
    ],
)
def test_classification_json_fields_round_trip_and_canonicalize(
    model_cls: type,
) -> None:
    instance = model_cls(
        name="classification-choice-json-test",
        subcategories=VALID_SUBCATEGORIES,
        numerical_descriptors=VALID_NUMERICAL_DESCRIPTORS,
    )

    instance.clean()

    assert instance.subcategories == VALID_SUBCATEGORIES
    assert instance.numerical_descriptors == VALID_NUMERICAL_DESCRIPTORS


@pytest.mark.parametrize(
    ("field_name", "payload"),
    [
        ("subcategories", {"extent": {"choices": []}}),
        (
            "subcategories",
            {"extent": {"choices": ["focal"], "default": 1, "required": True}},
        ),
        (
            "subcategories",
            {"extent": {"choices": ["focal"], "default": "diffuse"}},
        ),
        (
            "subcategories",
            {
                "extent": {
                    "choices": ["focal"],
                    "probability": ["not-a-number"],
                }
            },
        ),
        (
            "subcategories",
            {"extent": {"choices": ["focal"], "probability": [1.1]}},
        ),
        (
            "subcategories",
            {"extent": {"choices": ["focal"], "required": 1}},
        ),
        (
            "numerical_descriptors",
            {"size": {"required": False}},
        ),
        (
            "numerical_descriptors",
            {
                "size": {
                    "unit": "mm",
                    "required": False,
                    "min": float("nan"),
                    "max": 1.0,
                    "mean": None,
                    "std": None,
                    "default": None,
                    "distribution": "uniform",
                }
            },
        ),
        ("subcategories", []),
    ],
)
def test_classification_json_contract_rejects_invalid_payloads(
    field_name: str, payload: object
) -> None:
    with pytest.raises(ValueError):
        validate_classification_choice_json(payload, field_name=field_name)


def test_model_clean_reports_invalid_classification_json() -> None:
    instance = Disease(
        name="invalid-classification-json-test",
        subcategories={"extent": {"choices": []}},
        numerical_descriptors={},
    )

    with pytest.raises(ValidationError) as exc_info:
        instance.clean()

    assert "subcategories" in exc_info.value.message_dict


@pytest.mark.parametrize(
    ("field_name", "payload"),
    [
        (
            "subcategories",
            {
                "extent": {
                    "required": True,
                    "choices": ["focal", "diffuse"],
                    "value": "focal",
                }
            },
        ),
        (
            "numerical_descriptors",
            {
                "size": {
                    "min": 0.0,
                    "max": 10.0,
                    "distribution": "uniform",
                    "mean": 5.0,
                    "std": 1.0,
                    "value": 4.0,
                }
            },
        ),
    ],
)
def test_runtime_classification_json_round_trips(
    field_name: str,
    payload: dict[str, dict[str, object]],
) -> None:
    assert (
        validate_classification_choice_json(
            payload,
            field_name=field_name,
        )
        == payload
    )


def test_runtime_classification_json_rejects_value_outside_choices() -> None:
    with pytest.raises(ValueError, match="runtime contract"):
        validate_classification_choice_json(
            {
                "extent": {
                    "choices": ["focal"],
                    "value": "diffuse",
                }
            },
            field_name="subcategories",
        )


def test_classification_definitions_project_to_patient_runtime_payloads() -> None:
    subcategories = build_patient_finding_subcategories(VALID_SUBCATEGORIES)
    numerical_descriptors = build_patient_finding_numerical_descriptors(
        VALID_NUMERICAL_DESCRIPTORS
    )

    assert subcategories == {
        "extent": {
            "required": True,
            "choices": ["focal", "diffuse"],
            "value": "focal",
        }
    }
    assert numerical_descriptors == {
        "size": {
            "min": 0.0,
            "max": 100.0,
            "distribution": "uniform",
            "mean": 0.5,
            "std": 0.1,
            "value": None,
        }
    }
