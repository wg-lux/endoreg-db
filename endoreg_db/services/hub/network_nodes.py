from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, cast

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.schemas import (
    NetworkNodeCreatePayload,
    NetworkNodePayloadValidationError,
    NetworkNodeUpdatePayload,
    validate_network_node_create_payload,
    validate_network_node_update_payload,
)

_UNCHANGED_OWNING_CENTER: Final = object()


@dataclass(frozen=True, slots=True)
class _NetworkNodeUpdatePlan:
    field_updates: dict[str, object]
    owning_center: Center | None | object
    shared_secret: str | None
    clear_shared_secret: bool


@dataclass(frozen=True, slots=True)
class _NetworkNodeCreatePlan:
    display_name: str
    role: str
    base_url: str
    node_key: str
    shared_secret: str | None
    is_active: bool
    owning_center: Center | None


class NetworkNodeValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Network node validation failed.")
        self.errors = errors


def _raise_for_errors(errors: dict[str, str]) -> None:
    if errors:
        raise NetworkNodeValidationError(errors)


def _raw_reference_errors(
    value: object,
    *,
    errors: dict[str, str],
    node: NetworkNode | None = None,
) -> None:
    """Preserve useful DB identity errors when structural validation also fails."""
    if not isinstance(value, Mapping):
        return
    raw_payload = cast(Mapping[object, object], value)

    node_key = raw_payload.get("node_key")
    if "node_key" not in errors and isinstance(node_key, str):
        normalized_node_key = node_key.strip()
        if node is not None and normalized_node_key != node.node_key:
            errors["node_key"] = "node_key is immutable once assigned."
        elif (
            node is None
            and normalized_node_key
            and NetworkNode.objects.filter(node_key=normalized_node_key).exists()
        ):
            errors["node_key"] = "node_key already exists."

    if "owning_center" in errors:
        return
    center_id = raw_payload.get("owning_center_id")
    center_key = raw_payload.get("owning_center_key")
    normalized_center_key = center_key.strip() if isinstance(center_key, str) else ""
    valid_center_id = (
        isinstance(center_id, int) and not isinstance(center_id, bool) and center_id > 0
    )
    valid_center_key = bool(normalized_center_key)
    if valid_center_id and not Center.objects.filter(pk=center_id).exists():
        errors["owning_center"] = "Owning center not found."
    elif (
        valid_center_key
        and not Center.objects.filter(center_key=normalized_center_key).exists()
    ):
        errors["owning_center"] = "Owning center not found."


def _validate_create_boundary(value: object) -> NetworkNodeCreatePayload:
    try:
        return validate_network_node_create_payload(value)
    except NetworkNodePayloadValidationError as exc:
        errors = dict(exc.errors)
        _raw_reference_errors(value, errors=errors)
        raise NetworkNodeValidationError(errors) from exc


def _validate_update_boundary(
    node: NetworkNode,
    value: object,
) -> NetworkNodeUpdatePayload:
    try:
        return validate_network_node_update_payload(value)
    except NetworkNodePayloadValidationError as exc:
        errors = dict(exc.errors)
        _raw_reference_errors(value, errors=errors, node=node)
        raise NetworkNodeValidationError(errors) from exc


def _center_fields_supplied(
    payload: NetworkNodeCreatePayload | NetworkNodeUpdatePayload,
) -> tuple[bool, bool]:
    fields_set = payload.model_fields_set
    return "owning_center_id" in fields_set, "owning_center_key" in fields_set


def _resolve_owning_center(
    payload: NetworkNodeCreatePayload | NetworkNodeUpdatePayload,
    *,
    unchanged_when_omitted: bool,
    errors: dict[str, str],
) -> Center | None | object:
    has_id, has_key = _center_fields_supplied(payload)
    if not has_id and not has_key:
        return _UNCHANGED_OWNING_CENTER if unchanged_when_omitted else None

    center_by_id: Center | None = None
    center_by_key: Center | None = None
    if has_id and payload.owning_center_id not in (None, 0):
        center_by_id = Center.objects.filter(pk=payload.owning_center_id).first()
        if center_by_id is None:
            errors["owning_center"] = "Owning center not found."
    if has_key and payload.owning_center_key not in (None, ""):
        center_by_key = Center.objects.filter(
            center_key=payload.owning_center_key
        ).first()
        if center_by_key is None:
            errors["owning_center"] = "Owning center not found."

    if "owning_center" in errors:
        return None
    if has_id and has_key and center_by_id != center_by_key:
        errors["owning_center"] = (
            "owning_center_id and owning_center_key identify different centers."
        )
        return None
    return center_by_id if has_id else center_by_key


def _build_network_node_create_plan(value: object) -> _NetworkNodeCreatePlan:
    payload = _validate_create_boundary(value)
    errors: dict[str, str] = {}
    if (
        payload.node_key
        and NetworkNode.objects.filter(node_key=payload.node_key).exists()
    ):
        errors["node_key"] = "node_key already exists."
    owning_center = _resolve_owning_center(
        payload,
        unchanged_when_omitted=False,
        errors=errors,
    )
    _raise_for_errors(errors)
    return _NetworkNodeCreatePlan(
        display_name=payload.display_name,
        role=str(payload.role),
        base_url=payload.base_url,
        node_key=payload.node_key,
        shared_secret=payload.shared_secret,
        is_active=payload.is_active,
        owning_center=(owning_center if isinstance(owning_center, Center) else None),
    )


def _persist_network_node_create_plan(
    plan: _NetworkNodeCreatePlan,
) -> NetworkNode:
    node = NetworkNode(
        display_name=plan.display_name,
        role=plan.role,
        base_url=plan.base_url,
        is_active=plan.is_active,
        owning_center=plan.owning_center,
    )
    if plan.node_key:
        node.node_key = plan.node_key
    if plan.shared_secret:
        node.set_shared_secret(plan.shared_secret)
    node.save()
    node.refresh_from_db()
    return node


@transaction.atomic
def create_network_node(value: object) -> NetworkNode:
    return _persist_network_node_create_plan(_build_network_node_create_plan(value))


def _build_network_node_update_plan(
    node: NetworkNode,
    value: object,
) -> _NetworkNodeUpdatePlan:
    payload = _validate_update_boundary(node, value)
    fields_set = payload.model_fields_set
    errors: dict[str, str] = {}
    updates: dict[str, object] = {}

    if "node_key" in fields_set and payload.node_key != node.node_key:
        errors["node_key"] = "node_key is immutable once assigned."
    if "display_name" in fields_set and payload.display_name is not None:
        updates["display_name"] = payload.display_name
    if "role" in fields_set and payload.role is not None:
        updates["role"] = str(payload.role)
    if "base_url" in fields_set and payload.base_url is not None:
        updates["base_url"] = payload.base_url
    if "is_active" in fields_set and payload.is_active is not None:
        updates["is_active"] = payload.is_active

    owning_center = _resolve_owning_center(
        payload,
        unchanged_when_omitted=True,
        errors=errors,
    )
    _raise_for_errors(errors)
    return _NetworkNodeUpdatePlan(
        field_updates=updates,
        owning_center=owning_center,
        shared_secret=(
            payload.shared_secret if "shared_secret" in fields_set else None
        ),
        clear_shared_secret=(
            payload.clear_shared_secret is True
            if "clear_shared_secret" in fields_set
            else False
        ),
    )


def _apply_network_node_update_plan(
    node: NetworkNode,
    plan: _NetworkNodeUpdatePlan,
) -> None:
    for field_name, value in plan.field_updates.items():
        setattr(node, field_name, value)
    if isinstance(plan.owning_center, Center) or plan.owning_center is None:
        node.owning_center = plan.owning_center
    if plan.shared_secret:
        node.set_shared_secret(plan.shared_secret)
    if plan.clear_shared_secret:
        node.shared_secret_hash = ""


@transaction.atomic
def update_network_node(node: NetworkNode, value: object) -> NetworkNode:
    plan = _build_network_node_update_plan(node, value)
    _apply_network_node_update_plan(node, plan)
    node.save()
    node.refresh_from_db()
    return node


@transaction.atomic
def delete_network_node(node: NetworkNode) -> None:
    node.delete()


__all__ = [
    "NetworkNodeValidationError",
    "create_network_node",
    "delete_network_node",
    "update_network_node",
]
