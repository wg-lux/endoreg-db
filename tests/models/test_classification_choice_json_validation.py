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
            {
                "extent": {
                    "choices": ["focal"],
                    "probability": ["not-a-number"],
                }
            },
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
