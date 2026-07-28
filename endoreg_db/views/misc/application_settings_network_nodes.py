from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.services.hub.network_nodes import (
    NetworkNodeValidationError,
    create_network_node,
    delete_network_node,
    update_network_node,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission


def _request_payload(data: object) -> dict[str, Any]:
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _network_node_payload(node: NetworkNode) -> dict[str, Any]:
    owning_center = cast(Center | None, getattr(node, "owning_center", None))
    owning_center_id = model_pk(owning_center) if owning_center is not None else None
    node_role = cast(str, getattr(node, "role"))
    try:
        role_label = str(NetworkNode.Role(node_role).label)
    except ValueError:
        role_label = node_role
    created_at = cast(datetime | None, getattr(node, "created_at", None))
    updated_at = cast(datetime | None, getattr(node, "updated_at", None))

    return {
        "id": model_pk(node),
        "node_key": cast(str, getattr(node, "node_key", "")),
        "display_name": cast(str, getattr(node, "display_name", "")),
        "role": node_role,
        "role_label": role_label,
        "base_url": cast(str, getattr(node, "base_url", "")),
        "is_active": cast(bool, getattr(node, "is_active", False)),
        "owning_center_id": owning_center_id,
        "owning_center_key": (
            cast(str, getattr(owning_center, "center_key", ""))
            if owning_center is not None
            else None
        ),
        "owning_center_name": (
            cast(str, getattr(owning_center, "name", ""))
            if owning_center is not None
            else None
        ),
        "has_shared_secret": bool(cast(str, getattr(node, "shared_secret_hash", ""))),
        "created_at": created_at.isoformat() if created_at is not None else None,
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
    }


def _validation_error_response(exc: NetworkNodeValidationError) -> Response:
    return Response(
        {"errors": exc.errors},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _create_network_node_response(data: object) -> Response:
    try:
        node = create_network_node(_request_payload(data))
    except NetworkNodeValidationError as exc:
        return _validation_error_response(exc)
    return Response(_network_node_payload(node), status=status.HTTP_201_CREATED)


def _update_network_node_response(node: NetworkNode, data: object) -> Response:
    try:
        updated_node = update_network_node(node, _request_payload(data))
    except NetworkNodeValidationError as exc:
        return _validation_error_response(exc)
    return Response(
        _network_node_payload(updated_node),
        status=status.HTTP_200_OK,
    )


def _network_node_roles_payload() -> list[dict[str, str]]:
    return [
        {"value": choice.value, "label": str(choice.label)}
        for choice in NetworkNode.Role
    ]


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_network_nodes(request: Request) -> Response:
    if request.method == "POST":
        return _create_network_node_response(request.data)

    nodes = NetworkNode.objects.select_related("owning_center").order_by(
        "display_name",
        "pk",
    )
    return Response(
        [_network_node_payload(node) for node in nodes],
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_network_node_detail(request: Request, pk: int) -> Response:
    node = NetworkNode.objects.select_related("owning_center").filter(pk=pk).first()
    if node is None:
        return Response(
            {"detail": "Network node not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    if request.method == "GET":
        return Response(_network_node_payload(node), status=status.HTTP_200_OK)
    if request.method == "DELETE":
        delete_network_node(node)
        return Response(status=status.HTTP_204_NO_CONTENT)
    return _update_network_node_response(node, request.data)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_network_node_roles_dropdown(request: Request) -> Response:
    return Response(_network_node_roles_payload(), status=status.HTTP_200_OK)


__all__ = [
    "application_settings_network_nodes",
    "application_settings_network_node_detail",
    "application_settings_network_node_roles_dropdown",
]
