from __future__ import annotations

from typing import Any

from django.http import Http404

from endoreg_db.services.hub import resolve_allowed_center_id


def _extract_nested_attr(obj: Any, path: tuple[str, ...]) -> Any:
    current = obj
    for attr in path:
        if current is None:
            return None
        current = getattr(current, attr, None)
    return current


def resolve_object_center_id(obj: Any) -> int | None:
    direct_candidates = (
        getattr(obj, "center_id", None),
        getattr(obj, "source_center_id", None),
    )
    for candidate in direct_candidates:
        if isinstance(candidate, int):
            return candidate

    nested_paths = (
        ("center", "id"),
        ("patient", "center_id"),
        ("patient_examination", "patient", "center_id"),
        ("sensitive_meta", "center_id"),
        ("video", "center_id"),
    )
    for path in nested_paths:
        value = _extract_nested_attr(obj, path)
        if isinstance(value, int):
            return value

    return None


def assert_center_scope_allowed(
    *, request: Any, obj: Any, not_found_message: str = "Resource not found"
) -> None:
    allowed_center_id = resolve_allowed_center_id(getattr(request, "user", None))
    if allowed_center_id is None:
        return
    if allowed_center_id == -1:
        raise Http404(not_found_message)

    object_center_id = resolve_object_center_id(obj)
    if object_center_id is None or int(object_center_id) != int(allowed_center_id):
        raise Http404(not_found_message)
