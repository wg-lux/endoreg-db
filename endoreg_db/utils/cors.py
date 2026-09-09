from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from endoreg_db.config.env import get_django_cors_allowed_origins


def configured_cors_allowed_origins() -> list[str]:
    return get_django_cors_allowed_origins()


def resolve_response_origin(request: object) -> str | None:
    allowed = configured_cors_allowed_origins()
    if not allowed:
        return None

    headers = getattr(request, "headers", None)
    request_headers: Mapping[str, object] = (
        cast(Mapping[str, object], headers) if isinstance(headers, Mapping) else {}
    )
    request_origin = str(request_headers.get("Origin", "")).strip()
    if request_origin and request_origin in allowed:
        return request_origin

    return allowed[0]
