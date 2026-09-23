from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast


def request_payload_dict(request: object) -> dict[str, Any]:
    request_data = cast(object, getattr(request, "data", None))
    if request_data is None:
        request_data = cast(object, getattr(request, "POST", {}))
    if not isinstance(request_data, Mapping):
        return {}
    return {
        str(key): value
        for key, value in cast(Mapping[object, Any], request_data).items()
    }


__all__ = ["request_payload_dict"]
