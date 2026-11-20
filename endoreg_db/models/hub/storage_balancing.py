from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Unpack

from django.db import models
from django.db.models import Q

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from endoreg_db.models.hub.storage_placement import (
        StorageArtifactPlacement,
        StorageNodeState,
        StorageReservation,
        StorageRotation,
        StorageRotationCleanupReceipt,
    )


class StorageBalanceReason(models.TextChoices):
    DRAIN = "drain", "Drain"
    CAPACITY_PRESSURE = "capacity_pressure", "Capacity Pressure"


class StorageBalanceWorkStatus(models.TextChoices):
    ROTATION_REQUESTED = "rotation_requested", "Rotation Requested"
    BLOCKED = "blocked", "Blocked"


class StorageBalanceWorkItem(models.Model):
    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    source_placement: models.ForeignKey["StorageArtifactPlacement"] = models.ForeignKey(
        "StorageArtifactPlacement",
        on_delete=models.PROTECT,
        related_name="balance_work_items",
    )
    target_placement: models.ForeignKey["StorageArtifactPlacement | None"] = (
        models.ForeignKey(
            "StorageArtifactPlacement",
            null=True,
            blank=True,
            on_delete=models.PROTECT,
            related_name="inbound_balance_work_items",
        )
    )
    reservation: models.OneToOneField["StorageReservation | None"] = (
        models.OneToOneField(
            "StorageReservation",
            null=True,
            blank=True,
            on_delete=models.PROTECT,
            related_name="balance_work_item",
        )
    )
    rotation: models.OneToOneField["StorageRotation | None"] = models.OneToOneField(
        "StorageRotation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="balance_work_item",
    )
    reason: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageBalanceReason.choices,
    )
    status: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageBalanceWorkStatus.choices,
    )
    policy_version: models.CharField[Any, Any] = models.CharField(max_length=64)
    source_observation_version: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    artifact_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    artifact_kind: models.CharField[Any, Any] = models.CharField(max_length=32)
    expected_size_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    sha256: models.CharField[Any, Any] = models.CharField(max_length=64)
    placement_generation: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        unique=True,
    )
    terminal_reason: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        source_placement_id: uuid.UUID
        target_placement_id: uuid.UUID | None
        reservation_id: uuid.UUID | None
        rotation_id: uuid.UUID | None

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(expected_size_bytes__gt=0),
                name="balance_work_size_positive",
            ),
            models.CheckConstraint(
                condition=Q(placement_generation__gt=0),
                name="balance_work_generation_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="rotation_requested",
                        reservation__isnull=False,
                        target_placement__isnull=False,
                        rotation__isnull=False,
                        terminal_reason="",
                    )
                    | (
                        Q(status="blocked", reservation__isnull=True)
                        & Q(target_placement__isnull=True, rotation__isnull=True)
                        & ~Q(terminal_reason="")
                    )
                ),
                name="balance_work_status_payload_consistent",
            ),
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        normalized_hash = self.sha256.lower()
        if len(normalized_hash) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_hash
        ):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        self.sha256 = normalized_hash
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage balance work items are immutable")
        super().save(*args, **kwargs)


class StorageBalanceCancellationReceipt(models.Model):
    """Immutable evidence that pre-copy balance work was compensated."""

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    work_item: models.OneToOneField["StorageBalanceWorkItem"] = models.OneToOneField(
        StorageBalanceWorkItem,
        on_delete=models.PROTECT,
        related_name="cancellation_receipt",
    )
    rotation: models.OneToOneField["StorageRotation"] = models.OneToOneField(
        "StorageRotation",
        on_delete=models.PROTECT,
        related_name="balance_cancellation_receipt",
    )
    reservation: models.OneToOneField["StorageReservation"] = models.OneToOneField(
        "StorageReservation",
        on_delete=models.PROTECT,
        related_name="balance_cancellation_receipt",
    )
    actor: models.CharField[Any, Any] = models.CharField(max_length=255)
    reason: models.CharField[Any, Any] = models.CharField(max_length=255)
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    rotation_from_state: models.CharField[Any, Any] = models.CharField(max_length=24)
    rotation_target_state: models.CharField[Any, Any] = models.CharField(max_length=24)
    reservation_from_status: models.CharField[Any, Any] = models.CharField(
        max_length=16
    )
    reservation_target_status: models.CharField[Any, Any] = models.CharField(
        max_length=16
    )
    cancelled_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        work_item_id: uuid.UUID
        rotation_id: uuid.UUID
        reservation_id: uuid.UUID

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    rotation_from_state="requested",
                    rotation_target_state="failed",
                    reservation_from_status="active",
                    reservation_target_status="released",
                ),
                name="balance_cancel_exact_compensation",
            )
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        normalized_fingerprint = self.request_fingerprint.lower()
        if len(normalized_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_fingerprint
        ):
            raise ValueError(
                "request_fingerprint must be a 64-character hexadecimal digest"
            )
        self.request_fingerprint = normalized_fingerprint
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage balance cancellation receipts are immutable")
        super().save(*args, **kwargs)


class StorageReconciliationClassification(models.TextChoices):
    HEALTHY = "healthy", "Healthy"
    AUTHORIZED_ABSENCE = "authorized_absence", "Authorized Absence"
    DATABASE_ONLY = "database_only", "Database Only"
    STORAGE_ONLY = "storage_only", "Storage Only"
    DUPLICATE = "duplicate", "Duplicate"
    STALE_GENERATION = "stale_generation", "Stale Generation"
    CORRUPT = "corrupt", "Corrupt"
    UNREACHABLE = "unreachable", "Unreachable"


class StorageReconciliationSeverity(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    CRITICAL = "critical", "Critical"


class StorageReconciliationAlertCode(models.TextChoices):
    NONE = "none", "None"
    DATABASE_ONLY = "storage_database_only", "Database-only artifact"
    STORAGE_ONLY = "storage_untracked_artifact", "Storage-only artifact"
    DUPLICATE = "storage_duplicate_copy", "Duplicate storage copy"
    STALE_GENERATION = "storage_stale_generation", "Stale generation"
    INTEGRITY_MISMATCH = "storage_integrity_mismatch", "Integrity mismatch"
    UNREACHABLE_NODE = "storage_node_unreachable", "Unreachable storage node"
    STALE_HEALTH = "storage_health_stale", "Stale storage health"
    LOW_CAPACITY = "storage_capacity_low", "Low storage capacity"
    STOP_CAPACITY = "storage_capacity_stop", "Stop storage capacity"
    IMBALANCE = "storage_capacity_imbalance", "Storage capacity imbalance"
    STUCK_ROTATION = "storage_rotation_stuck", "Stuck storage rotation"
    RESERVATION_LEAK = "storage_reservation_expired", "Expired reservation"
    REPEATED_RETRY = "storage_rotation_repeated_retry", "Repeated rotation retry"
    CLEANUP_FAILURE = "storage_cleanup_failed", "Storage cleanup failure"
    ONLY_VERIFIED_COPY_LOST = (
        "storage_only_verified_copy_lost",
        "Only verified copy lost",
    )


class StorageReconciliationRun(models.Model):
    """Immutable evidence for one bounded, resumable reconciliation page."""

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    policy_version: models.CharField[Any, Any] = models.CharField(max_length=64)
    requested_by: models.CharField[Any, Any] = models.CharField(max_length=255)
    resume_cursor: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    next_cursor: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    observation_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    discrepancy_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    operational_event_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    observed_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    completed_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        normalized_fingerprint = self.request_fingerprint.lower()
        if len(normalized_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_fingerprint
        ):
            raise ValueError(
                "request_fingerprint must be a 64-character hexadecimal digest"
            )
        self.request_fingerprint = normalized_fingerprint
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage reconciliation runs are immutable")
        super().save(*args, **kwargs)


class StorageReconciliationObservation(models.Model):
    """Immutable storage-node evidence; it never selects a canonical copy."""

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    run: models.ForeignKey["StorageReconciliationRun"] = models.ForeignKey(
        StorageReconciliationRun,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    sequence: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField()
    storage_node: models.ForeignKey["StorageNodeState"] = models.ForeignKey(
        "StorageNodeState",
        on_delete=models.PROTECT,
        related_name="reconciliation_observations",
    )
    placement: models.ForeignKey["StorageArtifactPlacement | None"] = models.ForeignKey(
        "StorageArtifactPlacement",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reconciliation_observations",
    )
    artifact_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    artifact_kind: models.CharField[Any, Any] = models.CharField(max_length=32)
    reachable: models.BooleanField[Any, Any] = models.BooleanField()
    remote_present: models.BooleanField[Any, Any] = models.BooleanField()
    remote_copy_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    remote_generation: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(null=True, blank=True)
    )
    remote_size_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(null=True, blank=True)
    )
    remote_sha256: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    observed_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        run_id: uuid.UUID
        storage_node_id: int
        placement_id: uuid.UUID | None

    class Meta:
        ordering = ["run_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "sequence"],
                name="unique_storage_reconciliation_sequence",
            ),
            models.CheckConstraint(
                condition=(
                    Q(remote_present=True, remote_copy_count__gt=0)
                    | Q(
                        remote_present=False,
                        remote_copy_count=0,
                        remote_generation__isnull=True,
                        remote_size_bytes__isnull=True,
                        remote_sha256="",
                    )
                ),
                name="storage_reconciliation_remote_payload_consistent",
            ),
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if self.remote_sha256:
            normalized_hash = self.remote_sha256.lower()
            if len(normalized_hash) != 64 or any(
                character not in "0123456789abcdef" for character in normalized_hash
            ):
                raise ValueError(
                    "remote_sha256 must be a 64-character hexadecimal digest"
                )
            self.remote_sha256 = normalized_hash
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage reconciliation observations are immutable")
        super().save(*args, **kwargs)


class StorageReconciliationOutcome(models.Model):
    """Immutable classified result and stable alert for one observation."""

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    observation: models.OneToOneField["StorageReconciliationObservation"] = (
        models.OneToOneField(
            StorageReconciliationObservation,
            on_delete=models.PROTECT,
            related_name="outcome",
        )
    )
    cleanup_receipt: models.ForeignKey["StorageRotationCleanupReceipt | None"] = (
        models.ForeignKey(
            "StorageRotationCleanupReceipt",
            null=True,
            blank=True,
            on_delete=models.PROTECT,
            related_name="reconciliation_outcomes",
        )
    )
    classification: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageReconciliationClassification.choices,
    )
    severity: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=StorageReconciliationSeverity.choices,
    )
    alert_code: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        choices=StorageReconciliationAlertCode.choices,
    )
    correlation_id: models.CharField[Any, Any] = models.CharField(max_length=255)
    requires_operator_approval: models.BooleanField[Any, Any] = models.BooleanField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        observation_id: uuid.UUID
        cleanup_receipt_id: uuid.UUID | None

    class Meta:
        ordering = ["created_at", "pk"]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage reconciliation outcomes are immutable")
        super().save(*args, **kwargs)


class StorageReconciliationEvent(models.Model):
    """Immutable correlated event for bounded database recovery observations."""

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    run: models.ForeignKey["StorageReconciliationRun"] = models.ForeignKey(
        StorageReconciliationRun,
        on_delete=models.PROTECT,
        related_name="operational_events",
    )
    sequence: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField()
    alert_code: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        choices=StorageReconciliationAlertCode.choices,
    )
    severity: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=StorageReconciliationSeverity.choices,
    )
    correlation_id: models.CharField[Any, Any] = models.CharField(max_length=255)
    storage_node: models.ForeignKey["StorageNodeState | None"] = models.ForeignKey(
        "StorageNodeState",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reconciliation_events",
    )
    reservation: models.ForeignKey["StorageReservation | None"] = models.ForeignKey(
        "StorageReservation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reconciliation_events",
    )
    rotation: models.ForeignKey["StorageRotation | None"] = models.ForeignKey(
        "StorageRotation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reconciliation_events",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        run_id: uuid.UUID
        storage_node_id: int | None
        reservation_id: uuid.UUID | None
        rotation_id: uuid.UUID | None

    class Meta:
        ordering = ["run_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "sequence"],
                name="unique_storage_reconciliation_event_sequence",
            )
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage reconciliation events are immutable")
        super().save(*args, **kwargs)


class StorageHealthSnapshot(models.Model):
    """Immutable aggregate health for the exact reconciliation run."""

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    run: models.OneToOneField["StorageReconciliationRun"] = models.OneToOneField(
        StorageReconciliationRun,
        on_delete=models.PROTECT,
        related_name="health_snapshot",
    )
    node_count: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField()
    unreachable_node_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    stale_health_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    low_capacity_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    stop_capacity_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    critical_alert_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    warning_alert_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField()
    )
    observed_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        run_id: uuid.UUID

    class Meta:
        ordering = ["created_at", "pk"]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage health snapshots are immutable")
        super().save(*args, **kwargs)


class StorageOperatorAction(models.TextChoices):
    PAUSE = "pause", "Pause"
    RESUME = "resume", "Resume"
    RECONCILE = "reconcile", "Reconcile"
    REBALANCE = "rebalance", "Rebalance"
    RETRY = "retry", "Retry"


class StorageBalancingControlState(models.Model):
    """Concurrency-safe singleton for the current global balancing pause state."""

    _allow_control_transition: bool = False

    singleton_key: models.CharField[Any, Any] = models.CharField(
        primary_key=True,
        max_length=32,
        default="global",
        editable=False,
    )
    is_paused: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    version: models.PositiveBigIntegerField[Any, Any] = models.PositiveBigIntegerField(
        default=0
    )
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(singleton_key="global"),
                name="storage_balancing_control_singleton",
            )
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if self.singleton_key != "global":
            raise ValueError("storage balancing control state is a global singleton")
        persisted = type(self).objects.filter(pk="global").first()
        if (
            persisted is not None
            and (
                persisted.is_paused != self.is_paused
                or persisted.version != self.version
            )
            and not self._allow_control_transition
        ):
            raise ValueError(
                "storage balancing control changes require the operator service"
            )
        super().save(*args, **kwargs)

    def apply_control_transition(self, *, is_paused: bool, version: int) -> None:
        if version != self.version + 1:
            raise ValueError("storage balancing control version must advance by one")
        self.is_paused = is_paused
        self.version = version
        self._allow_control_transition = True
        try:
            self.save(update_fields=["is_paused", "version", "updated_at"])
        finally:
            self._allow_control_transition = False


class StorageOperatorControlReceipt(models.Model):
    """Immutable attributable operator intent; it never performs byte work."""

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    control_state: models.ForeignKey["StorageBalancingControlState"] = (
        models.ForeignKey(
            StorageBalancingControlState,
            on_delete=models.PROTECT,
            related_name="receipts",
        )
    )
    action: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=StorageOperatorAction.choices,
    )
    actor: models.CharField[Any, Any] = models.CharField(max_length=255)
    reason: models.CharField[Any, Any] = models.CharField(max_length=255)
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    control_version: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    storage_node: models.ForeignKey["StorageNodeState | None"] = models.ForeignKey(
        "StorageNodeState",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="operator_control_receipts",
    )
    work_item: models.ForeignKey["StorageBalanceWorkItem | None"] = models.ForeignKey(
        StorageBalanceWorkItem,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="operator_control_receipts",
    )
    rotation: models.ForeignKey["StorageRotation | None"] = models.ForeignKey(
        "StorageRotation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="operator_control_receipts",
    )
    source_placement: models.ForeignKey["StorageArtifactPlacement | None"] = (
        models.ForeignKey(
            "StorageArtifactPlacement",
            null=True,
            blank=True,
            on_delete=models.PROTECT,
            related_name="operator_control_receipts",
        )
    )
    paused_from: models.BooleanField[Any, Any] = models.BooleanField(
        null=True,
        blank=True,
    )
    paused_to: models.BooleanField[Any, Any] = models.BooleanField(
        null=True,
        blank=True,
    )
    retry_from_state: models.CharField[Any, Any] = models.CharField(
        max_length=24,
        blank=True,
        default="",
    )
    retry_target_semantics: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        storage_node_id: int | None
        work_item_id: uuid.UUID | None
        rotation_id: uuid.UUID | None
        source_placement_id: uuid.UUID | None

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        action__in=["pause", "resume"],
                        storage_node__isnull=True,
                        work_item__isnull=True,
                        rotation__isnull=True,
                        source_placement__isnull=True,
                        paused_from__isnull=False,
                        paused_to__isnull=False,
                        retry_from_state="",
                        retry_target_semantics="",
                    )
                    | Q(
                        action__in=["reconcile", "rebalance"],
                        work_item__isnull=True,
                        rotation__isnull=True,
                        source_placement__isnull=True,
                        paused_from__isnull=True,
                        paused_to__isnull=True,
                        retry_from_state="",
                        retry_target_semantics="",
                    )
                    | Q(
                        action="retry",
                        storage_node__isnull=True,
                        work_item__isnull=False,
                        rotation__isnull=False,
                        source_placement__isnull=False,
                        paused_from__isnull=True,
                        paused_to__isnull=True,
                        retry_from_state="failed",
                        retry_target_semantics="fresh_placement_required",
                    )
                ),
                name="storage_operator_control_payload_consistent",
            ),
            models.UniqueConstraint(
                fields=["work_item"],
                condition=Q(action="retry"),
                name="unique_storage_retry_intent_per_work",
            ),
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        normalized_fingerprint = self.request_fingerprint.lower()
        if len(normalized_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_fingerprint
        ):
            raise ValueError(
                "request_fingerprint must be a 64-character hexadecimal digest"
            )
        self.request_fingerprint = normalized_fingerprint
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("storage operator control receipts are immutable")
        super().save(*args, **kwargs)


__all__ = [
    "StorageBalanceCancellationReceipt",
    "StorageBalanceReason",
    "StorageBalanceWorkItem",
    "StorageBalanceWorkStatus",
    "StorageHealthSnapshot",
    "StorageBalancingControlState",
    "StorageOperatorAction",
    "StorageOperatorControlReceipt",
    "StorageReconciliationAlertCode",
    "StorageReconciliationClassification",
    "StorageReconciliationEvent",
    "StorageReconciliationObservation",
    "StorageReconciliationOutcome",
    "StorageReconciliationRun",
    "StorageReconciliationSeverity",
]
