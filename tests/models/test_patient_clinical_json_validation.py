from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest

from endoreg_db.schemas.patient_clinical import (
    validate_patient_finding_numerical_descriptors,
    validate_patient_finding_subcategories,
    validate_patient_numerical_descriptors,
    validate_patient_subcategories,
)


def test_patient_definition_contracts_canonicalize_aliases() -> None:
    assert validate_patient_subcategories(
        {"grade": {"choices": ["I", "II"], "default": "I", "required": True}}
    ) == {"grade": {"choices": ["I", "II"], "default": "I", "required": True}}
    assert validate_patient_numerical_descriptors(
        {
            "size": {
                "unit": "mm",
                "required": False,
                "min": 0.0,
                "max": 10.0,
                "mean": 5.0,
                "std": 1.0,
                "default": 5.0,
                "distribution": "normal",
            }
        }
    ) == {
        "size": {
            "unit": "mm",
            "required": False,
            "min": 0.0,
            "max": 10.0,
            "mean": 5.0,
            "std": 1.0,
            "default": 5.0,
            "distribution": "normal",
        }
    }


@pytest.mark.parametrize(
    ("validator", "payload"),
    [
        (validate_patient_subcategories, {"x": {"choices": ["a"]}}),
        (validate_patient_subcategories, {1: {"choices": ["a"]}}),
        (
            validate_patient_subcategories,
            {"x": {"choices": ["a"], "default": "a", "required": 1}},
        ),
        (validate_patient_numerical_descriptors, {"x": {"unit": "mm"}}),
        (
            validate_patient_numerical_descriptors,
            {
                "x": {
                    "unit": "mm",
                    "required": False,
                    "min": math.nan,
                    "max": 1.0,
                    "mean": 0.5,
                    "std": 0.1,
                    "default": 0.5,
                    "distribution": "normal",
                }
            },
        ),
        (validate_patient_numerical_descriptors, []),
    ],
)
def test_patient_definition_contracts_reject_invalid_payloads(
    validator: Callable[[Any], dict[str, Any]], payload: object
) -> None:
    with pytest.raises(ValueError):
        validator(payload)


def test_patient_finding_runtime_contracts_canonicalize_values() -> None:
    assert validate_patient_finding_subcategories(
        {"status": {"choices": ["present", "absent"], "value": "present"}}
    ) == {
        "status": {
            "required": False,
            "choices": ["present", "absent"],
            "value": "present",
        }
    }
    assert (
        validate_patient_finding_numerical_descriptors(
            {"score": {"min": 0.0, "max": 1.0, "value": 0.75}}
        )["score"]["value"]
        == 0.75
    )


@pytest.mark.parametrize(
    ("validator", "payload"),
    [
        (validate_patient_finding_subcategories, {"status": {"choices": []}}),
        (
            validate_patient_finding_subcategories,
            {"status": {"choices": ["a"], "value": "b"}},
        ),
        (
            validate_patient_finding_numerical_descriptors,
            {"score": {"min": 2.0, "max": 1.0}},
        ),
        (
            validate_patient_finding_numerical_descriptors,
            {"score": {"value": math.inf}},
        ),
        (validate_patient_finding_subcategories, {1: {"choices": ["a"]}}),
        (validate_patient_finding_subcategories, []),
    ],
)
def test_patient_finding_runtime_contracts_reject_invalid_payloads(
    validator: Callable[[Any], dict[str, Any]], payload: object
) -> None:
    with pytest.raises(ValueError):
        validator(payload)
