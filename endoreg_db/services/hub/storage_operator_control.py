from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction

from endoreg_db.models.hub.storage_balancing import (
    StorageBalanceWorkItem,
    StorageBalanceWorkStatus,
    StorageBalancingControlState,
    StorageOperatorAction,
    StorageOperatorControlReceipt,
)
from endoreg_db.models.hub.storage_placement import (
    StorageArtifactPlacement,
    StorageNodeState,
    StorageReservation,
    StorageRotation,
)

STORAGE_OPERATOR_CONTROL_CONTRACT_VERSION = "hub-storage-operator-control-v1"
STORAGE_RETRY_TARGET_SEMANTICS = "fresh_placement_required"


class StorageOperatorControlErrorCode(StrEnum):
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    ACTION_BLOCKED_WHILE_PAUSED = "action_blocked_while_paused"
    RETRY_ALREADY_REQUESTED = "retry_already_requested"
    RETRY_NOT_SAFE = "retry_not_safe"
    TARGET_NOT_FOUND = "target_not_found"


class StorageOperatorControlError(RuntimeError):
    def __init__(self, code: StorageOperatorControlErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _validate_attribution(*, actor: str, reason: str, idempotency_key: str) -> None:
    for name, value in (
        ("actor", actor),
        ("reason", reason),
        ("idempotency_key", idempotency_key),
    ):
        if not value.strip():
            raise ValueError(f"{name} must not be blank")
        if len(value) > 255:
            raise ValueError(f"{name} must not exceed 255 characters")


@dataclass(frozen=True, slots=True)
class StorageBalancingPauseRequest:
    paused: bool
    actor: str
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _validate_attribution(
            actor=self.actor,
            reason=self.reason,
            idempotency_key=self.idempotency_key,
        )


@dataclass(frozen=True, slots=True)
class StorageManualActionRequest:
    action: StorageOperatorAction
    actor: str
    reason: str
    idempotency_key: str
    storage_node_id: int | None = None

    def __post_init__(self) -> None:
        _validate_attribution(
            actor=self.actor,
            reason=self.reason,
            idempotency_key=self.idempotency_key,
        )
        if self.action not in {
            StorageOperatorAction.RECONCILE,
            StorageOperatorAction.REBALANCE,
        }:
            raise ValueError("manual action must be reconcile or rebalance")
        if self.storage_node_id is not None and self.storage_node_id <= 0:
            raise ValueError("storage_node_id must be positive when provided")


@dataclass(frozen=True, slots=True)
class StorageBalanceRetryRequest:
    work_item_id: UUID
    actor: str
    reason: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _validate_attribution(
            actor=self.actor,
            reason=self.reason,
            idempotency_key=self.idempotency_key,
        )


@dataclass(frozen=True, slots=True)
class _NormalizedRequest:
    action: StorageOperatorAction
    actor: str
    reason: str
    idempotency_key: str
    paused: bool | None = None
    storage_node_id: int | None = None
    work_item_id: UUID | None = None


def _fingerprint(request: _NormalizedRequest) -> str:
    canonical = json.dumps(
        {
            "contract_version": STORAGE_OPERATOR_CONTROL_CONTRACT_VERSION,
            "action": request.action.value,
            "actor": request.actor,
            "reason": request.reason,
            "paused": request.paused,
            "storage_node_id": request.storage_node_id,
            "work_item_id": str(request.work_item_id)
            if request.work_item_id is not None
            else None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _safe_retry_rows(
    work_item_id: UUID,
) -> tuple[StorageBalanceWorkItem, StorageRotation, StorageArtifactPlacement]:
    work_item = (
        StorageBalanceWorkItem.objects.select_for_update()
        .filter(pk=work_item_id)
        .first()
    )
    if work_item is None:
        raise StorageOperatorControlError(
            StorageOperatorControlErrorCode.TARGET_NOT_FOUND,
            "Storage balance work item does not exist.",
        )
    if (
        work_item.status != StorageBalanceWorkStatus.ROTATION_REQUESTED
        or work_item.rotation_id is None
        or work_item.target_placement_id is None
        or work_item.reservation_id is None
    ):
        raise StorageOperatorControlError(
            StorageOperatorControlErrorCode.RETRY_NOT_SAFE,
            "Only failed persisted rotation work can receive a retry intent.",
        )
    rotation = StorageRotation.objects.select_for_update().get(pk=work_item.rotation_id)
    source = StorageArtifactPlacement.objects.select_for_update().get(
        pk=work_item.source_placement_id
    )
    target = StorageArtifactPlacement.objects.select_for_update().get(
        pk=work_item.target_placement_id
    )
    reservation = StorageReservation.objects.select_for_update().get(
        pk=work_item.reservation_id
    )
    if (
        rotation.state != StorageRotation.State.FAILED
        or rotation.committed_at is not None
        or rotation.source_placement_id != source.pk
        or rotation.target_placement_id != target.pk
        or source.role != StorageArtifactPlacement.Role.PRIMARY
        or source.state != StorageArtifactPlacement.State.COMMITTED
        or target.state != StorageArtifactPlacement.State.FAILED
        or reservation.status
        not in {
            StorageReservation.Status.RELEASED,
            StorageReservation.Status.EXPIRED,
        }
        or target.reservation_id != reservation.pk
    ):
        raise StorageOperatorControlError(
            StorageOperatorControlErrorCode.RETRY_NOT_SAFE,
            "Retry requires an uncommitted failed target, released capacity, and the unchanged canonical source.",
        )
    return work_item, rotation, source


def _record_operator_control(
    *,
    request: _NormalizedRequest,
    fingerprint: str,
) -> StorageOperatorControlReceipt:
    with transaction.atomic():
        replay = (
            StorageOperatorControlReceipt.objects.select_for_update()
            .filter(idempotency_key=request.idempotency_key)
            .first()
        )
        if replay is not None:
            if replay.request_fingerprint != fingerprint:
                raise StorageOperatorControlError(
                    StorageOperatorControlErrorCode.IDEMPOTENCY_CONFLICT,
                    "Operator-control idempotency key is bound to another request.",
                )
            return replay

        state = StorageBalancingControlState.objects.select_for_update().get(
            pk="global"
        )
        if state.is_paused and request.action in {
            StorageOperatorAction.REBALANCE,
            StorageOperatorAction.RETRY,
        }:
            raise StorageOperatorControlError(
                StorageOperatorControlErrorCode.ACTION_BLOCKED_WHILE_PAUSED,
                "Rebalance and retry intents are blocked while balancing is paused.",
            )

        storage_node: StorageNodeState | None = None
        work_item: StorageBalanceWorkItem | None = None
        rotation: StorageRotation | None = None
        source: StorageArtifactPlacement | None = None
        paused_from: bool | None = None
        paused_to: bool | None = None
        retry_from_state = ""
        retry_target_semantics = ""
        if request.storage_node_id is not None:
            storage_node = (
                StorageNodeState.objects.select_for_update()
                .filter(pk=request.storage_node_id)
                .first()
            )
            if storage_node is None:
                raise StorageOperatorControlError(
                    StorageOperatorControlErrorCode.TARGET_NOT_FOUND,
                    "Storage node does not exist.",
                )
        if request.action in {
            StorageOperatorAction.PAUSE,
            StorageOperatorAction.RESUME,
        }:
            if request.paused is None:
                raise ValueError("pause transition requires its target state")
            paused_from = state.is_paused
            paused_to = request.paused
        elif request.action == StorageOperatorAction.RETRY:
            if request.work_item_id is None:
                raise ValueError("retry intent requires work_item_id")
            prior_retry = (
                StorageOperatorControlReceipt.objects.select_for_update()
                .filter(
                    action=StorageOperatorAction.RETRY,
                    work_item_id=request.work_item_id,
                )
                .first()
            )
            if prior_retry is not None:
                raise StorageOperatorControlError(
                    StorageOperatorControlErrorCode.RETRY_ALREADY_REQUESTED,
                    "Failed work already has an immutable retry intent.",
                )
            work_item, rotation, source = _safe_retry_rows(request.work_item_id)
            retry_from_state = StorageRotation.State.FAILED
            retry_target_semantics = STORAGE_RETRY_TARGET_SEMANTICS

        next_version = state.version + 1
        receipt = StorageOperatorControlReceipt.objects.create(
            control_state=state,
            action=request.action.value,
            actor=request.actor,
            reason=request.reason,
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            control_version=next_version,
            storage_node=storage_node,
            work_item=work_item,
            rotation=rotation,
            source_placement=source,
            paused_from=paused_from,
            paused_to=paused_to,
            retry_from_state=retry_from_state,
            retry_target_semantics=retry_target_semantics,
        )
        state.apply_control_transition(
            is_paused=request.paused
            if request.action
            in {StorageOperatorAction.PAUSE, StorageOperatorAction.RESUME}
            and request.paused is not None
            else state.is_paused,
            version=next_version,
        )
        return receipt


def _record_with_replay(
    request: _NormalizedRequest,
) -> StorageOperatorControlReceipt:
    fingerprint = _fingerprint(request)
    try:
        return _record_operator_control(request=request, fingerprint=fingerprint)
    except IntegrityError as exc:
        replay = StorageOperatorControlReceipt.objects.filter(
            idempotency_key=request.idempotency_key
        ).first()
        if replay is not None and replay.request_fingerprint == fingerprint:
            return replay
        raise StorageOperatorControlError(
            StorageOperatorControlErrorCode.IDEMPOTENCY_CONFLICT,
            "Operator-control idempotency key is bound to another request.",
        ) from exc


def set_storage_balancing_paused(
    *, request: StorageBalancingPauseRequest
) -> StorageOperatorControlReceipt:
    action = (
        StorageOperatorAction.PAUSE if request.paused else StorageOperatorAction.RESUME
    )
    return _record_with_replay(
        _NormalizedRequest(
            action=action,
            actor=request.actor,
            reason=request.reason,
            idempotency_key=request.idempotency_key,
            paused=request.paused,
        )
    )


def request_manual_storage_action(
    *, request: StorageManualActionRequest
) -> StorageOperatorControlReceipt:
    return _record_with_replay(
        _NormalizedRequest(
            action=request.action,
            actor=request.actor,
            reason=request.reason,
            idempotency_key=request.idempotency_key,
            storage_node_id=request.storage_node_id,
        )
    )


def request_storage_balance_retry(
    *, request: StorageBalanceRetryRequest
) -> StorageOperatorControlReceipt:
    return _record_with_replay(
        _NormalizedRequest(
            action=StorageOperatorAction.RETRY,
            actor=request.actor,
            reason=request.reason,
            idempotency_key=request.idempotency_key,
            work_item_id=request.work_item_id,
        )
    )


def get_storage_balancing_control_state() -> StorageBalancingControlState:
    return StorageBalancingControlState.objects.get(pk="global")


__all__ = [
    "STORAGE_OPERATOR_CONTROL_CONTRACT_VERSION",
    "STORAGE_RETRY_TARGET_SEMANTICS",
    "StorageBalanceRetryRequest",
    "StorageBalancingPauseRequest",
    "StorageManualActionRequest",
    "StorageOperatorControlError",
    "StorageOperatorControlErrorCode",
    "get_storage_balancing_control_state",
    "request_manual_storage_action",
    "request_storage_balance_retry",
    "set_storage_balancing_paused",
]
