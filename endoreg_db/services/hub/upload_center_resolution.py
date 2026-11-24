"""Declared upload center resolution; orchestration lives in ingest.py."""

from __future__ import annotations

from endoreg_db.models.administration.center.center import Center


def decide_declared_upload_center(
    *,
    center_key: str,
    center_name: str,
    center_by_key: Center | None,
    center_by_name: Center | None,
) -> tuple[Center | None, str | None]:
    """Combine normalized declarations and their lookups into one center or error.

    When both are declared and exactly one is known, the known center is used.
    """
    if (center_by_key is not None and not center_key) or (
        center_by_name is not None and not center_name
    ):
        raise ValueError("Resolved center requires its declared identity")
    if center_by_key is not None and center_by_name is not None:
        if center_by_key.pk != center_by_name.pk:
            return None, "center_key and center_name refer to different centers"
        return center_by_key, None
    if center_by_key is not None:
        return center_by_key, None
    if center_by_name is not None:
        return center_by_name, None
    if center_key and center_name:
        return None, "Unknown center identity"
    if center_key:
        return None, f"Unknown center_key: {center_key}"
    if center_name:
        return None, f"Unknown center_name: {center_name}"
    return None, None


def resolve_declared_upload_center(
    *,
    center_key: str | None = None,
    center_name: str | None = None,
) -> tuple[Center | None, str | None]:
    normalized_center_key = (center_key or "").strip()
    normalized_center_name = (center_name or "").strip()

    if not normalized_center_key and not normalized_center_name:
        return None, None

    center_by_key = (
        Center.objects.filter(center_key=normalized_center_key).first()
        if normalized_center_key
        else None
    )
    center_by_name = (
        Center.objects.filter(name=normalized_center_name).first()
        if normalized_center_name
        else None
    )
    return decide_declared_upload_center(
        center_key=normalized_center_key,
        center_name=normalized_center_name,
        center_by_key=center_by_key,
        center_by_name=center_by_name,
    )
