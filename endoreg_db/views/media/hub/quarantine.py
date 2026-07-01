from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, TypeAlias, cast

from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.hub.quarantine_item import QuarantineItem
from endoreg_db.serializers.hub import (
    QuarantineDecisionRequestSerializer,
    QuarantineItemSerializer,
    QuarantineReapRequestSerializer,
    QuarantineSyncRequestSerializer,
)
from endoreg_db.services.hub.quarantine import (
    approve_quarantine_item,
    list_quarantine_items,
    reap_approved_quarantine_items,
    retain_quarantine_item,
    stale_pending_review_items,
    sync_quarantine_inventory,
    user_or_none,
)

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class _SerializerLike(Protocol):
    @property
    def data(self) -> JsonValue: ...


def _query_params(request: Request) -> Mapping[str, str]:
    return cast(Mapping[str, str], request.query_params)


def _query_int_param(params: Mapping[str, str], key: str, default: int) -> int:
    raw_value = params.get(key)
    if raw_value in ("", None):
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValidationError({key: "Must be an integer."}) from exc
    if value < 0:
        raise ValidationError({key: "Must not be negative."})
    return value


def _query_str_param(params: Mapping[str, str], key: str) -> str | None:
    raw_value = params.get(key)
    if raw_value is None:
        return None
    stripped = raw_value.strip()
    return stripped or None


def _serialize(serializer: _SerializerLike) -> JsonValue:
    return serializer.data


def _get_item(item_id: str) -> QuarantineItem:
    item = QuarantineItem.objects.filter(pk=item_id).first()
    if item is None:
        raise Http404("Quarantine item not found")
    return item


class QuarantineItemListView(APIView):
    permission_classes = [IsAuthenticated, PolicyPermission]

    def get(self, request: Request) -> Response:
        params = _query_params(request)
        limit = min(_query_int_param(params, "limit", 100), 500)
        offset = _query_int_param(params, "offset", 0)
        status_filter = _query_str_param(params, "status")
        older_than_days = (
            _query_int_param(params, "older_than_days", 30)
            if "older_than_days" in params
            else None
        )
        total_count, items = list_quarantine_items(
            status=status_filter,
            older_than_days=older_than_days,
            limit=limit,
            offset=offset,
        )
        serializer = QuarantineItemSerializer(items, many=True)
        return Response(
            {
                "count": total_count,
                "limit": limit,
                "offset": offset,
                "results": _serialize(cast(_SerializerLike, serializer)),
            }
        )


class QuarantineSyncView(APIView):
    permission_classes = [IsAuthenticated, PolicyPermission]

    def post(self, request: Request) -> Response:
        serializer = QuarantineSyncRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, object], serializer.validated_data)
        older_than_days = cast(int, data["older_than_days"])
        sync_result = sync_quarantine_inventory()
        stale_items = stale_pending_review_items(older_than_days=older_than_days)
        return Response(
            {
                "quarantine_dir": str(sync_result.quarantine_dir),
                "scanned_count": sync_result.scanned_count,
                "created_count": sync_result.created_count,
                "updated_count": sync_result.updated_count,
                "missing_count": sync_result.missing_count,
                "total_bytes": sync_result.total_bytes,
                "stale_pending_review_count": len(stale_items),
                "stale_pending_review": [
                    str(item.pk) for item in stale_items[: min(len(stale_items), 100)]
                ],
            },
            status=status.HTTP_200_OK,
        )


class QuarantineApproveDeletionView(APIView):
    permission_classes = [IsAuthenticated, PolicyPermission]

    def post(self, request: Request, item_id: str) -> Response:
        item = _get_item(item_id)
        serializer = QuarantineDecisionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, object], serializer.validated_data)
        try:
            updated = approve_quarantine_item(
                item,
                reason=cast(str, data["decision_reason"]),
                reviewed_by=user_or_none(request.user),
                delete_after_days=cast(int, data["delete_after_days"]),
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            _serialize(cast(_SerializerLike, QuarantineItemSerializer(updated)))
        )


class QuarantineRetainView(APIView):
    permission_classes = [IsAuthenticated, PolicyPermission]

    def post(self, request: Request, item_id: str) -> Response:
        item = _get_item(item_id)
        serializer = QuarantineDecisionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, object], serializer.validated_data)
        try:
            updated = retain_quarantine_item(
                item,
                reason=cast(str, data["decision_reason"]),
                reviewed_by=user_or_none(request.user),
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            _serialize(cast(_SerializerLike, QuarantineItemSerializer(updated)))
        )


class QuarantineReapApprovedView(APIView):
    permission_classes = [IsAuthenticated, PolicyPermission]

    def post(self, request: Request) -> Response:
        serializer = QuarantineReapRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict[str, object], serializer.validated_data)
        result = reap_approved_quarantine_items(
            older_than_days=cast(int, data["older_than_days"]),
            dry_run=cast(bool, data["dry_run"]),
        )
        return Response(
            {
                "quarantine_dir": str(result.quarantine_dir),
                "dry_run": result.dry_run,
                "candidate_count": result.candidate_count,
                "candidate_bytes": result.candidate_bytes,
                "deleted_count": result.deleted_count,
                "missing_count": result.missing_count,
                "candidates": [str(item.pk) for item in result.candidates],
                "deleted": [str(item.pk) for item in result.deleted],
            }
        )
