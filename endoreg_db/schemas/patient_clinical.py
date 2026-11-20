from __future__ import annotations

import math
from typing import Any, cast

from lx_dtypes.models.contracts.patient_finding_classification_runtime import (
    PatientFindingClassificationNumericalDescriptorsPayload,
    PatientFindingClassificationSubcategoriesPayload,
)
from pydantic import BaseModel, ValidationError
from lx_dtypes.models.contracts.subcategory_validation import (
    NumericalDescriptorContract,
    SubcategoryDictContract,
)


def _dump_mapping_contract(
    contract: type[BaseModel], value: Any, *, field_name: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    _reject_non_finite_or_coerced_values(value, field_name=field_name)
    normalized: dict[str, Any] = {}
    items = cast(dict[object, object], value)
    for key in items:
        item: object = items[key]
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        try:
            payload = contract.model_validate(item)
        except ValidationError as exc:
            raise ValueError(f"{field_name}.{key}: {exc}") from exc
        normalized[key] = payload.model_dump(mode="json", by_alias=True)
    return normalized


def _reject_non_finite_or_coerced_values(value: Any, *, field_name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field_name} does not allow NaN or infinite floats")
    if isinstance(value, dict):
        items = cast(dict[object, object], value)
        for key in items:
            item: object = items[key]
            if key == "required" and not isinstance(item, bool):
                raise ValueError(f"{field_name}.required must be a boolean")
            _reject_non_finite_or_coerced_values(item, field_name=field_name)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _reject_non_finite_or_coerced_values(item, field_name=field_name)


def validate_patient_subcategories(value: Any) -> dict[str, Any]:
    return _dump_mapping_contract(
        SubcategoryDictContract, value, field_name="subcategories"
    )


def validate_patient_numerical_descriptors(value: Any) -> dict[str, Any]:
    return _dump_mapping_contract(
        NumericalDescriptorContract,
        value,
        field_name="numerical_descriptors",
    )


def _dump_runtime_contract(
    contract: type[BaseModel], value: Any, *, field_name: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    _reject_non_finite_or_coerced_values(value, field_name=field_name)
    try:
        payload = contract.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"{field_name}: {exc}") from exc
    dumped = payload.model_dump(mode="json")
    return dumped


def validate_patient_finding_subcategories(value: Any) -> dict[str, Any]:
    return _dump_runtime_contract(
        PatientFindingClassificationSubcategoriesPayload,
        value,
        field_name="subcategories",
    )


def validate_patient_finding_numerical_descriptors(value: Any) -> dict[str, Any]:
    return _dump_runtime_contract(
        PatientFindingClassificationNumericalDescriptorsPayload,
        value,
        field_name="numerical_descriptors",
    )


__all__ = [
    "validate_patient_finding_numerical_descriptors",
    "validate_patient_finding_subcategories",
    "validate_patient_numerical_descriptors",
    "validate_patient_subcategories",
]
