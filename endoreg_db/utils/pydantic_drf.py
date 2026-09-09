from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

PydanticModelT = TypeVar("PydanticModelT", bound=BaseModel)


def drf_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    source = cast(Mapping[object, Any], value)
    return {str(key): item for key, item in source.items()}


def drf_validation_error(exc: PydanticValidationError) -> DRFValidationError:
    return DRFValidationError(cast(Any, exc.errors(include_context=False)))


def drf_validation_error_detail(exc: PydanticValidationError) -> dict[str, Any]:
    return {"details": exc.errors(include_context=False)}


def drf_validation_error_response(
    exc: PydanticValidationError,
    *,
    message: str,
) -> Response:
    return Response(
        {"error": message, **drf_validation_error_detail(exc)},
        status=status.HTTP_400_BAD_REQUEST,
    )


def validate_drf_payload(
    model_cls: type[PydanticModelT],
    value: object,
) -> PydanticModelT:
    try:
        return model_cls.model_validate(drf_mapping(value))
    except PydanticValidationError as exc:
        raise drf_validation_error(exc) from exc
