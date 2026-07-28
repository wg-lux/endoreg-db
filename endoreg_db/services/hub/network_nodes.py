from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode

_UNCHANGED_OWNING_CENTER: Final = object()


@dataclass(frozen=True, slots=True)
class _NetworkNodeUpdatePlan:
    field_updates: dict[str, object]
    owning_center: Center | None | object
    shared_secret: str | None
    update_shared_secret: bool
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


def _record_field_error(
    errors: dict[str, str],
    *,
    field_name: str,
    error: str | None,
) -> None:
    if error is not None:
        errors[field_name] = error


def _resolve_center_from_payload(
    data: Mapping[str, Any],
    *,
    errors: dict[str, str],
) -> Center | None | object:
    if "owning_center_id" not in data and "owning_center_key" not in data:
        return _UNCHANGED_OWNING_CENTER

    center_value = data.get("owning_center_id", data.get("owning_center_key"))
    if center_value in (None, "", 0):
        return None

    if isinstance(center_value, int):
        center = Center.objects.filter(pk=center_value).first()
    else:
        center = Center.objects.filter(center_key=str(center_value).strip()).first()

    if center is None:
        errors["owning_center"] = "Owning center not found."
    return center


def _normalize_required_display_name(value: object) -> tuple[str, str | None]:
    display_name = str(value or "").strip()
    if not display_name:
        return "", "display_name is required."
    return display_name, None


def _normalize_role(value: object) -> tuple[str, str | None]:
    role = str(value or NetworkNode.Role.SITE_NODE).strip()
    if role not in NetworkNode.Role.values:
        return role, "Invalid role."
    return role, None


def _normalize_is_active(value: object) -> tuple[bool, str | None]:
    if not isinstance(value, bool):
        return False, "is_active must be a boolean."
    return value, None


def _normalize_shared_secret(value: object) -> tuple[str | None, str | None]:
    if value is not None and not isinstance(value, str):
        return None, "shared_secret must be a string."
    return value, None


def _normalize_node_key(value: object) -> tuple[str, str | None]:
    node_key = str(value or "").strip()
    if node_key and NetworkNode.objects.filter(node_key=node_key).exists():
        return node_key, "node_key already exists."
    return node_key, None


def _build_network_node_create_plan(
    data: Mapping[str, Any],
) -> _NetworkNodeCreatePlan:
    display_name, display_name_error = _normalize_required_display_name(
        data.get("display_name")
    )
    role, role_error = _normalize_role(data.get("role"))
    is_active, is_active_error = _normalize_is_active(data.get("is_active", True))
    shared_secret, shared_secret_error = _normalize_shared_secret(
        data.get("shared_secret")
    )
    node_key, node_key_error = _normalize_node_key(data.get("node_key"))

    errors: dict[str, str] = {}
    _record_field_error(
        errors,
        field_name="display_name",
        error=display_name_error,
    )
    _record_field_error(errors, field_name="role", error=role_error)
    _record_field_error(errors, field_name="is_active", error=is_active_error)
    owning_center = _resolve_center_from_payload(data, errors=errors)
    _record_field_error(
        errors,
        field_name="shared_secret",
        error=shared_secret_error,
    )
    _record_field_error(errors, field_name="node_key", error=node_key_error)
    _raise_for_errors(errors)

    return _NetworkNodeCreatePlan(
        display_name=display_name,
        role=role,
        base_url=str(data.get("base_url", "") or "").strip(),
        node_key=node_key,
        shared_secret=shared_secret,
        is_active=is_active,
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
    if plan.shared_secret is not None and plan.shared_secret.strip():
        node.set_shared_secret(plan.shared_secret)
    node.save()
    node.refresh_from_db()
    return node


@transaction.atomic
def create_network_node(data: Mapping[str, Any]) -> NetworkNode:
    return _persist_network_node_create_plan(_build_network_node_create_plan(data))


def _validate_node_key_update(
    node: NetworkNode,
    data: Mapping[str, Any],
    *,
    errors: dict[str, str],
) -> None:
    if "node_key" not in data:
        return
    requested_node_key = str(data.get("node_key", "") or "").strip()
    if requested_node_key and requested_node_key != node.node_key:
        errors["node_key"] = "node_key is immutable once assigned."


def _collect_display_name_update(
    data: Mapping[str, Any],
    *,
    errors: dict[str, str],
    updates: dict[str, object],
) -> None:
    if "display_name" not in data:
        return
    display_name = str(data.get("display_name", "") or "").strip()
    if not display_name:
        errors["display_name"] = "display_name must not be blank."
        return
    updates["display_name"] = display_name


def _collect_role_update(
    data: Mapping[str, Any],
    *,
    errors: dict[str, str],
    updates: dict[str, object],
) -> None:
    if "role" not in data:
        return
    role = str(data.get("role", "") or "").strip()
    if role not in NetworkNode.Role.values:
        errors["role"] = "Invalid role."
        return
    updates["role"] = role


def _collect_base_url_update(
    data: Mapping[str, Any],
    *,
    updates: dict[str, object],
) -> None:
    if "base_url" in data:
        updates["base_url"] = str(data.get("base_url", "") or "").strip()


def _collect_is_active_update(
    data: Mapping[str, Any],
    *,
    errors: dict[str, str],
    updates: dict[str, object],
) -> None:
    if "is_active" not in data:
        return
    is_active = data.get("is_active")
    if not isinstance(is_active, bool):
        errors["is_active"] = "is_active must be a boolean."
        return
    updates["is_active"] = is_active


def _resolve_shared_secret_update(
    data: Mapping[str, Any],
    *,
    errors: dict[str, str],
) -> tuple[str | None, bool]:
    if "shared_secret" not in data:
        return None, False
    shared_secret = data.get("shared_secret")
    if not isinstance(shared_secret, str):
        errors["shared_secret"] = "shared_secret must be a string."
        return None, False
    return shared_secret, bool(shared_secret.strip())


def _resolve_clear_shared_secret(
    data: Mapping[str, Any],
    *,
    errors: dict[str, str],
) -> bool:
    clear_shared_secret = data.get("clear_shared_secret")
    if clear_shared_secret is True:
        return True
    if "clear_shared_secret" not in data or clear_shared_secret is False:
        return False
    errors["clear_shared_secret"] = "clear_shared_secret must be a boolean."
    return False


def _build_network_node_update_plan(
    node: NetworkNode,
    data: Mapping[str, Any],
) -> _NetworkNodeUpdatePlan:
    errors: dict[str, str] = {}
    updates: dict[str, object] = {}
    _validate_node_key_update(node, data, errors=errors)
    _collect_display_name_update(data, errors=errors, updates=updates)
    _collect_role_update(data, errors=errors, updates=updates)
    _collect_base_url_update(data, updates=updates)
    _collect_is_active_update(data, errors=errors, updates=updates)
    owning_center = _resolve_center_from_payload(data, errors=errors)
    shared_secret, update_shared_secret = _resolve_shared_secret_update(
        data,
        errors=errors,
    )
    clear_shared_secret = _resolve_clear_shared_secret(data, errors=errors)
    _raise_for_errors(errors)
    return _NetworkNodeUpdatePlan(
        field_updates=updates,
        owning_center=owning_center,
        shared_secret=shared_secret,
        update_shared_secret=update_shared_secret,
        clear_shared_secret=clear_shared_secret,
    )


def _apply_field_updates(
    node: NetworkNode,
    updates: Mapping[str, object],
) -> None:
    for field_name, value in updates.items():
        setattr(node, field_name, value)


def _apply_owning_center_update(
    node: NetworkNode,
    owning_center: Center | None | object,
) -> None:
    if isinstance(owning_center, Center) or owning_center is None:
        node.owning_center = owning_center


def _apply_shared_secret_updates(
    node: NetworkNode,
    *,
    plan: _NetworkNodeUpdatePlan,
) -> None:
    if plan.update_shared_secret and plan.shared_secret is not None:
        node.set_shared_secret(plan.shared_secret)
    if plan.clear_shared_secret:
        node.shared_secret_hash = ""


@transaction.atomic
def update_network_node(node: NetworkNode, data: Mapping[str, Any]) -> NetworkNode:
    plan = _build_network_node_update_plan(node, data)
    _apply_field_updates(node, plan.field_updates)
    _apply_owning_center_update(node, plan.owning_center)
    _apply_shared_secret_updates(node, plan=plan)
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
