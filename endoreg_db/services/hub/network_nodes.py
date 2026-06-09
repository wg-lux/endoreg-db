from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode

_UNCHANGED_OWNING_CENTER: Final = object()


class NetworkNodeValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("Network node validation failed.")
        self.errors = errors


def _raise_for_errors(errors: dict[str, str]) -> None:
    if errors:
        raise NetworkNodeValidationError(errors)


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


@transaction.atomic
def create_network_node(data: Mapping[str, Any]) -> NetworkNode:
    display_name = str(data.get("display_name", "") or "").strip()
    role = str(data.get("role", "") or NetworkNode.Role.SITE_NODE).strip()
    base_url = str(data.get("base_url", "") or "").strip()
    provided_node_key = str(data.get("node_key", "") or "").strip()
    shared_secret = data.get("shared_secret")
    is_active = data.get("is_active", True)

    errors: dict[str, str] = {}
    if not display_name:
        errors["display_name"] = "display_name is required."
    if role not in NetworkNode.Role.values:
        errors["role"] = "Invalid role."
    if not isinstance(is_active, bool):
        errors["is_active"] = "is_active must be a boolean."

    owning_center = _resolve_center_from_payload(data, errors=errors)
    if shared_secret is not None and not isinstance(shared_secret, str):
        errors["shared_secret"] = "shared_secret must be a string."

    if (
        provided_node_key
        and NetworkNode.objects.filter(node_key=provided_node_key).exists()
    ):
        errors["node_key"] = "node_key already exists."

    _raise_for_errors(errors)

    node = NetworkNode(
        display_name=display_name,
        role=role,
        base_url=base_url,
        is_active=is_active,
        owning_center=owning_center if isinstance(owning_center, Center) else None,
    )
    if provided_node_key:
        node.node_key = provided_node_key
    if isinstance(shared_secret, str) and shared_secret.strip():
        node.set_shared_secret(shared_secret)
    node.save()
    node.refresh_from_db()
    return node


@transaction.atomic
def update_network_node(node: NetworkNode, data: Mapping[str, Any]) -> NetworkNode:
    errors: dict[str, str] = {}
    updates: dict[str, Any] = {}
    shared_secret = data.get("shared_secret")
    should_update_shared_secret = False
    should_clear_shared_secret = False

    if "node_key" in data:
        requested_node_key = str(data.get("node_key", "") or "").strip()
        if requested_node_key and requested_node_key != node.node_key:
            errors["node_key"] = "node_key is immutable once assigned."

    if "display_name" in data:
        display_name = str(data.get("display_name", "") or "").strip()
        if not display_name:
            errors["display_name"] = "display_name must not be blank."
        else:
            updates["display_name"] = display_name

    if "role" in data:
        role = str(data.get("role", "") or "").strip()
        if role not in NetworkNode.Role.values:
            errors["role"] = "Invalid role."
        else:
            updates["role"] = role

    if "base_url" in data:
        updates["base_url"] = str(data.get("base_url", "") or "").strip()

    if "is_active" in data:
        is_active = data.get("is_active")
        if not isinstance(is_active, bool):
            errors["is_active"] = "is_active must be a boolean."
        else:
            updates["is_active"] = is_active

    owning_center = _resolve_center_from_payload(data, errors=errors)

    if "shared_secret" in data:
        if not isinstance(shared_secret, str):
            errors["shared_secret"] = "shared_secret must be a string."
        elif shared_secret.strip():
            should_update_shared_secret = True

    if data.get("clear_shared_secret") is True:
        should_clear_shared_secret = True
    elif "clear_shared_secret" in data and data.get("clear_shared_secret") is not False:
        errors["clear_shared_secret"] = "clear_shared_secret must be a boolean."

    _raise_for_errors(errors)

    for field_name, value in updates.items():
        setattr(node, field_name, value)

    if isinstance(owning_center, Center) or owning_center is None:
        node.owning_center = owning_center

    if should_update_shared_secret and isinstance(shared_secret, str):
        node.set_shared_secret(shared_secret)
    if should_clear_shared_secret:
        node.shared_secret_hash = ""

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
