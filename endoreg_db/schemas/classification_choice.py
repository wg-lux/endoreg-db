"""Shared persistence validation for classification-choice JSON contracts."""

from __future__ import annotations

import math
from typing import Any, Literal, cast

from lx_dtypes.models.contracts.patient_finding_classification_runtime import (
    PatientFindingClassificationNumericalDescriptorsData,
    PatientFindingClassificationNumericalDescriptorsPayload,
    PatientFindingClassificationSubcategoriesData,
    PatientFindingClassificationSubcategoriesPayload,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)


class ClassificationSubcategoryDefinition(BaseModel):
    """Persisted knowledge-base definition accepted by EndoReg 0.2.9."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    choices: list[str]
    default: str | None = None
    required: bool | None = None
    probability: list[float] | None = None
    description: str | None = None

    @model_validator(mode="after")
    def validate_choice_references(self) -> "ClassificationSubcategoryDefinition":
        if not self.choices:
            raise ValueError("choices must not be empty")
        if self.default is not None and self.default not in self.choices:
            raise ValueError("default must be one of choices")
        if self.probability is not None and any(
            probability < 0.0 or probability > 1.0 for probability in self.probability
        ):
            raise ValueError("probability values must be between 0 and 1")
        return self


class ClassificationNumericalDescriptorDefinition(BaseModel):
    """Persisted numerical descriptor definition used by the YAML knowledge base."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    unit: str
    required: bool
    minimum: float | None = Field(default=None, alias="min")
    maximum: float | None = Field(default=None, alias="max")
    mean: float | None = None
    std: float | None = None
    default: float | None = None
    distribution: Literal["normal", "uniform"] | None = None
    description: str | None = None


_Subcategories = TypeAdapter(dict[str, ClassificationSubcategoryDefinition])
_NumericalDescriptors = TypeAdapter(
    dict[str, ClassificationNumericalDescriptorDefinition]
)


class ClassificationChoiceJSONValidationError(ValueError):
    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.field_name = field_name


def _reject_non_finite_or_coerced_values(value: Any, *, field_name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ClassificationChoiceJSONValidationError(
            field_name, f"{field_name} does not allow NaN or infinite floats"
        )
    if isinstance(value, dict):
        items = cast(dict[object, object], value)
        for key in items:
            item: object = items[key]
            if key == "required" and not isinstance(item, bool):
                raise ClassificationChoiceJSONValidationError(
                    field_name, f"{field_name}.required must be a boolean"
                )
            _reject_non_finite_or_coerced_values(item, field_name=field_name)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _reject_non_finite_or_coerced_values(item, field_name=field_name)


def validate_classification_choice_json(
    value: Any,
    *,
    field_name: str,
) -> dict[str, dict[str, Any]]:
    """Validate and canonicalize one classification-choice JSON mapping.

    Empty mappings remain valid because the models have historically used them
    as the default for choices without metadata.  Non-empty mappings must use
    the strict shared lx_dtypes contracts.
    """

    if not isinstance(value, dict):
        raise ClassificationChoiceJSONValidationError(
            field_name, f"{field_name} must be a JSON object"
        )
    _reject_non_finite_or_coerced_values(value, field_name=field_name)

    # Finding choices use the runtime shape (``value``), while the other
    # classification choices use definition metadata (``default``).
    mapping = cast(dict[object, object], value)
    if any(isinstance(item, dict) and "value" in item for item in mapping.values()):
        runtime_contract = (
            PatientFindingClassificationSubcategoriesPayload
            if field_name == "subcategories"
            else PatientFindingClassificationNumericalDescriptorsPayload
        )
        try:
            runtime_payload = runtime_contract.model_validate(value)
        except ValidationError as exc:
            raise ClassificationChoiceJSONValidationError(
                field_name,
                f"{field_name} does not match the runtime contract: {exc}",
            ) from exc
        return runtime_payload.model_dump(mode="json")

    adapter = _Subcategories if field_name == "subcategories" else _NumericalDescriptors
    try:
        validated = adapter.validate_python(value)
    except ValidationError as exc:
        raise ClassificationChoiceJSONValidationError(
            field_name, f"{field_name} does not match the shared contract: {exc}"
        ) from exc

    return {
        key: item.model_dump(mode="json", by_alias=True, exclude_unset=True)
        for key, item in validated.items()
    }


def validate_classification_choice_json_fields(instance: Any) -> None:
    """Normalize both persisted JSON fields on a classification model."""

    instance.subcategories = validate_classification_choice_json(
        instance.subcategories,
        field_name="subcategories",
    )
    instance.numerical_descriptors = validate_classification_choice_json(
        instance.numerical_descriptors,
        field_name="numerical_descriptors",
    )


def build_patient_finding_subcategories(
    value: Any,
) -> PatientFindingClassificationSubcategoriesData:
    """Project a knowledge-base definition into patient runtime state."""

    definitions = validate_classification_choice_json(
        value,
        field_name="subcategories",
    )
    runtime_payload = {
        name: {
            "required": definition.get("required", False),
            "choices": definition["choices"],
            "value": definition.get("default"),
        }
        for name, definition in definitions.items()
    }
    return cast(
        PatientFindingClassificationSubcategoriesData,
        PatientFindingClassificationSubcategoriesPayload.model_validate(
            runtime_payload
        ).model_dump(mode="json"),
    )


def build_patient_finding_numerical_descriptors(
    value: Any,
) -> PatientFindingClassificationNumericalDescriptorsData:
    """Project numerical knowledge-base definitions into patient runtime state."""

    definitions = validate_classification_choice_json(
        value,
        field_name="numerical_descriptors",
    )
    runtime_payload: dict[str, dict[str, object]] = {}
    runtime_keys = ("min", "max", "distribution", "mean", "std")
    for name, definition in definitions.items():
        descriptor = {
            key: definition[key]
            for key in runtime_keys
            if definition.get(key) is not None
        }
        descriptor["value"] = definition.get("default")
        runtime_payload[name] = descriptor
    return cast(
        PatientFindingClassificationNumericalDescriptorsData,
        PatientFindingClassificationNumericalDescriptorsPayload.model_validate(
            runtime_payload
        ).model_dump(mode="json"),
    )


__all__ = [
    "ClassificationNumericalDescriptorDefinition",
    "ClassificationChoiceJSONValidationError",
    "ClassificationSubcategoryDefinition",
    "build_patient_finding_numerical_descriptors",
    "build_patient_finding_subcategories",
    "validate_classification_choice_json",
    "validate_classification_choice_json_fields",
]
