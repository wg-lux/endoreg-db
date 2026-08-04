from __future__ import annotations

from endoreg_db.config.env import get_django_cors_allowed_origins


def configured_cors_allowed_origins() -> list[str]:
    return get_django_cors_allowed_origins()


def resolve_response_origin(request) -> str | None:
    allowed = configured_cors_allowed_origins()
    if not allowed:
        return None

    request_origin = (request.headers.get("Origin") or "").strip()
    if request_origin and request_origin in allowed:
        return request_origin

    return allowed[0]
