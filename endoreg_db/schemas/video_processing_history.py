from __future__ import annotations

from typing import Any, cast

from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import ConfigDict, TypeAdapter


_JSON_OBJECT_ADAPTER: TypeAdapter[JsonObject] = TypeAdapter(
    JsonObject,
    config=ConfigDict(strict=True, allow_inf_nan=False),
)


def validate_video_processing_history_config(value: Any) -> JsonObject:
    """Validate and canonicalize a persisted processing-history config.

    Operation-specific contracts are applied by the job consumers. The model
    boundary guarantees that every history config is at least a strict JSON
    object, so all operation variants share one safe persistence shape.
    """

    validated: JsonObject = _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    return cast(JsonObject, _JSON_OBJECT_ADAPTER.dump_python(validated, mode="json"))


__all__ = ["validate_video_processing_history_config"]
