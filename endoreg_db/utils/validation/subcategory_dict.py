from typing import Protocol

from pydantic import ValidationError

from endoreg_db.models import Unit
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.subcategory_validation import (
    NumericalDescriptorContract,
    SubcategoryDictContract,
)


class _ValidatorOwner(Protocol):
    pass


def validate_subcategory_dict(
    self: _ValidatorOwner, subcategory_dict: JsonObject | None = None
) -> bool:
    if subcategory_dict is None:
        return False

    try:
        validated = SubcategoryDictContract.model_validate(subcategory_dict)
    except ValidationError:
        return False

    return validated.default in validated.choices


def validate_numerical_descriptor(
    self: _ValidatorOwner, numerical_descriptor_dict: JsonObject | None = None
) -> tuple[bool, str | None]:
    if numerical_descriptor_dict is None:
        return False, "numerical_descriptor_dict is None"

    try:
        validated = NumericalDescriptorContract.model_validate(
            numerical_descriptor_dict
        )
    except ValidationError as exc:
        first_error = exc.errors()[0]
        location = ".".join(str(part) for part in first_error["loc"])
        message = first_error["msg"]
        return False, f"{location}: {message}"

    if not Unit.objects.filter(name=validated.unit).exists():
        return False, "Unit object with that name does not exist"

    return True, None
