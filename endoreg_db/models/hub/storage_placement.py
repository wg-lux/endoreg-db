from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Unpack

from django.db import models
from django.db.models import Q

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from endoreg_db.models.hub.network_node import NetworkNode
    from endoreg_db.models.hub.storage_transfer import StorageTransferEvidence
    from endoreg_db.models.media.video.video_file import VideoFile


def _normalize_sha256(value: str) -> str:
    normalized_hash = value.lower()
    if len(normalized_hash) != 64 or any(
        character not in "0123456789abcdef" for character in normalized_hash
    ):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")
    return normalized_hash


class StorageArtifactKind(models.TextChoices):
    ANONYMIZED_VIDEO = "anonymized_video", "Anonymized Video"
    PROCESSED_REPORT = "processed_report", "Processed Report"
    VIDEO_HLS = "video_hls", "Video HTTP Live Streaming"
    STREAMABLE_VIDEO = "streamable_video", "Streamable Video"
    SIDECAR = "sidecar", "Sidecar"
    MANIFEST = "manifest", "Manifest"


class StorageNodeState(models.Model):
    node: models.OneToOneField["NetworkNode"] = models.OneToOneField(
        "NetworkNode",
        on_delete=models.PROTECT,
        related_name="storage_state",
    )
    is_draining: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    is_reachable: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    accepting_writes: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    last_probe_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    last_error_code: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    failure_domain: models.CharField[Any, Any] = models.CharField(max_length=128)
    residency_key: models.CharField[Any, Any] = models.CharField(max_length=128)
    placement_weight: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField(default=100)
    )
    total_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    filesystem_free_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    policy_usable_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    reserved_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(default=0)
    )
    in_flight_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(default=0)
    )
    committed_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(default=0)
    )
    cleanup_reclaimable_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(default=0)
    )
    observed_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    observation_version: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(default=1)
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        node_id: int
        capability_rows: models.Manager["StorageNodeCapability"]

    class Meta:
        ordering = ["node__node_key"]
        constraints = [
            models.CheckConstraint(
                condition=Q(placement_weight__gt=0),
                name="storage_node_weight_positive",
            ),
            models.CheckConstraint(
                condition=Q(policy_usable_bytes__lte=models.F("total_bytes")),
                name="storage_usable_within_total",
            ),
            models.CheckConstraint(
                condition=Q(filesystem_free_bytes__lte=models.F("total_bytes")),
                name="storage_free_within_total",
            ),
            models.CheckConstraint(
                condition=Q(
                    reserved_bytes__lte=(
                        models.F("policy_usable_bytes")
                        - models.F("in_flight_bytes")
                        - models.F("committed_bytes")
                    )
                ),
                name="storage_accounted_within_usable",
            ),
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        from endoreg_db.models.hub.network_node import NetworkNode

        if self.node_id:
            role = NetworkNode.objects.values_list("role", flat=True).get(
                pk=self.node_id
            )
            if role != NetworkNode.Role.STORAGE_NODE:
                raise ValueError("StorageNodeState requires a storage_node role")
            if self.pk:
                persisted_node_id = (
                    type(self)
                    .objects.filter(pk=self.pk)
                    .values_list("node_id", flat=True)
                    .first()
                )
                if persisted_node_id is not None and persisted_node_id != self.node_id:
                    raise ValueError("storage node identity is immutable")
        super().save(*args, **kwargs)


class StorageNodeCapability(models.Model):
    storage_node: models.ForeignKey["StorageNodeState"] = models.ForeignKey(
        StorageNodeState,
        on_delete=models.CASCADE,
        related_name="capability_rows",
    )
    artifact_kind: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageArtifactKind.choices,
    )

    class Meta:
        ordering = ["storage_node_id", "artifact_kind"]
        constraints = [
            models.UniqueConstraint(
                fields=["storage_node", "artifact_kind"],
                name="unique_storage_node_capability",
            )
        ]


class StorageReservation(models.Model):
    _allow_status_transition: bool = False

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CONSUMED = "consumed", "Consumed"
        RELEASED = "released", "Released"
        EXPIRED = "expired", "Expired"

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    storage_node: models.ForeignKey["StorageNodeState"] = models.ForeignKey(
        StorageNodeState,
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    artifact_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    artifact_kind: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageArtifactKind.choices,
    )
    requested_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    policy_version: models.CharField[Any, Any] = models.CharField(max_length=64)
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    status: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    expires_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        storage_node_id: int
        placement: "StorageArtifactPlacement"

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(requested_bytes__gt=0),
                name="storage_reservation_bytes_positive",
            ),
            models.UniqueConstraint(
                fields=["artifact_key", "artifact_kind"],
                condition=Q(status="active"),
                name="unique_active_artifact_reservation",
            ),
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if self.pk:
            persisted_status = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )
            if (
                persisted_status is not None
                and persisted_status != self.status
                and not self._allow_status_transition
            ):
                raise ValueError(
                    "reservation status changes require the lifecycle service"
                )
        super().save(*args, **kwargs)

    def apply_lifecycle_status(
        self, target_status: "StorageReservation.Status"
    ) -> None:
        self.status = target_status.value
        self._allow_status_transition = True
        try:
            self.save(update_fields=["status", "updated_at"])
        finally:
            self._allow_status_transition = False


class StorageReservationTransition(models.Model):
    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    reservation: models.ForeignKey["StorageReservation"] = models.ForeignKey(
        StorageReservation,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    from_status: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=StorageReservation.Status.choices,
    )
    target_status: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=StorageReservation.Status.choices,
    )
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "target_status"],
                name="unique_reservation_target_transition",
            )
        ]


class StorageArtifactPlacement(models.Model):
    _allow_state_transition: bool = False

    class Role(models.TextChoices):
        PRIMARY = "primary", "Primary"
        REPLICA = "replica", "Replica"

    class State(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        COPYING = "copying", "Copying"
        VERIFIED = "verified", "Verified"
        COMMITTED = "committed", "Committed"
        SUPERSEDED = "superseded", "Superseded"
        LOST = "lost", "Lost"
        FAILED = "failed", "Failed"

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    artifact_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    artifact_kind: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageArtifactKind.choices,
    )
    storage_node: models.ForeignKey["StorageNodeState"] = models.ForeignKey(
        StorageNodeState,
        on_delete=models.PROTECT,
        related_name="placements",
    )
    media_lease_video: models.ForeignKey["VideoFile | None"] = models.ForeignKey(
        "VideoFile",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="storage_artifact_placements",
    )
    reservation: models.OneToOneField["StorageReservation | None"] = (
        models.OneToOneField(
            StorageReservation,
            null=True,
            blank=True,
            on_delete=models.PROTECT,
            related_name="placement",
        )
    )
    role: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.PRIMARY,
    )
    state: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.RESERVED,
    )
    generation: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField(default=1)
    )
    expected_size_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    sha256: models.CharField[Any, Any] = models.CharField(max_length=64)
    policy_version: models.CharField[Any, Any] = models.CharField(max_length=64)
    committed_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True,
        blank=True,
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        storage_node_id: int
        reservation_id: uuid.UUID | None
        media_lease_video_id: int | None

    class Meta:
        ordering = ["artifact_kind", "artifact_key", "generation", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(generation__gt=0),
                name="storage_placement_generation_positive",
            ),
            models.CheckConstraint(
                condition=Q(expected_size_bytes__gt=0),
                name="storage_placement_size_positive",
            ),
            models.UniqueConstraint(
                fields=["artifact_key", "artifact_kind"],
                condition=Q(role="primary", state="committed"),
                name="unique_committed_primary_placement",
            ),
            models.UniqueConstraint(
                fields=["artifact_key", "artifact_kind", "storage_node", "generation"],
                name="unique_artifact_node_generation",
            ),
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.sha256 = _normalize_sha256(self.sha256)
        if self.state == self.State.COMMITTED and self.committed_at is None:
            raise ValueError("committed placement requires committed_at")
        if self.pk:
            persisted_state = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("state", flat=True)
                .first()
            )
            if (
                persisted_state is not None
                and persisted_state != self.state
                and not self._allow_state_transition
            ):
                raise ValueError(
                    "placement state changes require the lifecycle service"
                )
        super().save(*args, **kwargs)

    def apply_lifecycle_state(
        self,
        target_state: "StorageArtifactPlacement.State",
        *,
        role: "StorageArtifactPlacement.Role | None" = None,
        committed_at: datetime | None = None,
    ) -> None:
        self.state = target_state.value
        update_fields = ["state", "updated_at"]
        if role is not None:
            self.role = role.value
            update_fields.append("role")
        if committed_at is not None:
            self.committed_at = committed_at
            update_fields.append("committed_at")
        self._allow_state_transition = True
        try:
            self.save(update_fields=update_fields)
        finally:
            self._allow_state_transition = False


class StorageRotation(models.Model):
    _allow_state_transition: bool = False

    class State(models.TextChoices):
        REQUESTED = "requested", "Requested"
        COPYING = "copying", "Copying"
        COPIED = "copied", "Copied"
        VERIFIED = "verified", "Verified"
        COMMITTED = "committed", "Committed"
        CLEANUP_DEFERRED = "cleanup_deferred", "Cleanup Deferred"
        CLEANED = "cleaned", "Cleaned"
        FAILED = "failed", "Failed"

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    artifact_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    artifact_kind: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageArtifactKind.choices,
    )
    source_placement: models.ForeignKey["StorageArtifactPlacement"] = models.ForeignKey(
        StorageArtifactPlacement,
        on_delete=models.PROTECT,
        related_name="outbound_rotations",
    )
    target_placement: models.ForeignKey["StorageArtifactPlacement"] = models.ForeignKey(
        StorageArtifactPlacement,
        on_delete=models.PROTECT,
        related_name="inbound_rotations",
    )
    expected_size_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    sha256: models.CharField[Any, Any] = models.CharField(max_length=64)
    policy_version: models.CharField[Any, Any] = models.CharField(max_length=64)
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    initiated_by: models.CharField[Any, Any] = models.CharField(max_length=255)
    reason: models.CharField[Any, Any] = models.CharField(max_length=255)
    state: models.CharField[Any, Any] = models.CharField(
        max_length=24,
        choices=State.choices,
        default=State.REQUESTED,
    )
    retry_count: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField(
        default=0
    )
    terminal_failure_reason: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)
    verified_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    committed_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    cleaned_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )

    if TYPE_CHECKING:
        source_placement_id: uuid.UUID
        target_placement_id: uuid.UUID

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(source_placement=models.F("target_placement")),
                name="storage_rotation_distinct_placements",
            ),
            models.CheckConstraint(
                condition=Q(expected_size_bytes__gt=0),
                name="storage_rotation_size_positive",
            ),
            models.UniqueConstraint(
                fields=["artifact_key", "artifact_kind"],
                condition=~Q(state__in=["cleaned", "failed"]),
                name="unique_active_storage_rotation",
            ),
        ]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.sha256 = _normalize_sha256(self.sha256)
        if self.pk:
            persisted_state = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("state", flat=True)
                .first()
            )
            if (
                persisted_state is not None
                and persisted_state != self.state
                and not self._allow_state_transition
            ):
                raise ValueError("rotation state changes require the lifecycle service")
        super().save(*args, **kwargs)

    def apply_lifecycle_state(
        self,
        target_state: "StorageRotation.State",
        *,
        transition_time: datetime,
        failure_reason: str = "",
    ) -> None:
        self.state = target_state.value
        update_fields = ["state", "updated_at"]
        if target_state == self.State.VERIFIED:
            self.verified_at = transition_time
            update_fields.append("verified_at")
        elif target_state == self.State.COMMITTED:
            self.committed_at = transition_time
            update_fields.append("committed_at")
        elif target_state == self.State.CLEANED:
            self.cleaned_at = transition_time
            update_fields.append("cleaned_at")
        elif target_state == self.State.FAILED:
            self.terminal_failure_reason = failure_reason.strip()
            update_fields.append("terminal_failure_reason")
        self._allow_state_transition = True
        try:
            self.save(update_fields=update_fields)
        finally:
            self._allow_state_transition = False


class StorageRotationVerificationReceipt(models.Model):
    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    rotation: models.OneToOneField["StorageRotation"] = models.OneToOneField(
        StorageRotation,
        on_delete=models.PROTECT,
        related_name="verification_receipt",
    )
    target_placement: models.ForeignKey["StorageArtifactPlacement"] = models.ForeignKey(
        StorageArtifactPlacement,
        on_delete=models.PROTECT,
        related_name="verification_receipts",
    )
    transfer_evidence: models.OneToOneField["StorageTransferEvidence | None"] = (
        models.OneToOneField(
            "StorageTransferEvidence",
            on_delete=models.PROTECT,
            related_name="rotation_verification_receipt",
            null=True,
            blank=True,
        )
    )
    artifact_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    artifact_kind: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageArtifactKind.choices,
    )
    target_node_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    expected_size_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    sha256: models.CharField[Any, Any] = models.CharField(max_length=64)
    placement_generation: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    verifier: models.CharField[Any, Any] = models.CharField(max_length=255)
    evidence_reference: models.CharField[Any, Any] = models.CharField(max_length=255)
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    verified_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        rotation_id: uuid.UUID
        target_placement_id: uuid.UUID

    class Meta:
        ordering = ["created_at", "pk"]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.sha256 = _normalize_sha256(self.sha256)
        super().save(*args, **kwargs)


class StorageRotationCleanupReceipt(models.Model):
    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    rotation: models.OneToOneField["StorageRotation"] = models.OneToOneField(
        StorageRotation,
        on_delete=models.PROTECT,
        related_name="cleanup_receipt",
    )
    verification_receipt: models.ForeignKey["StorageRotationVerificationReceipt"] = (
        models.ForeignKey(
            StorageRotationVerificationReceipt,
            on_delete=models.PROTECT,
            related_name="cleanup_receipts",
        )
    )
    source_transfer_evidence: models.OneToOneField["StorageTransferEvidence | None"] = (
        models.OneToOneField(
            "StorageTransferEvidence",
            null=True,
            blank=True,
            on_delete=models.PROTECT,
            related_name="rotation_cleanup_receipt",
        )
    )
    artifact_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    artifact_kind: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=StorageArtifactKind.choices,
    )
    source_node_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    target_node_key: models.CharField[Any, Any] = models.CharField(max_length=255)
    expected_size_bytes: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    sha256: models.CharField[Any, Any] = models.CharField(max_length=64)
    placement_generation: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    reconciler: models.CharField[Any, Any] = models.CharField(max_length=255)
    evidence_reference: models.CharField[Any, Any] = models.CharField(max_length=255)
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    media_leases_checked_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    replicas_checked_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    reconciled_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        rotation_id: uuid.UUID
        verification_receipt_id: uuid.UUID
        source_transfer_evidence_id: uuid.UUID | None

    class Meta:
        ordering = ["created_at", "pk"]

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.sha256 = _normalize_sha256(self.sha256)
        super().save(*args, **kwargs)


class StorageRotationTransition(models.Model):
    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    rotation: models.ForeignKey["StorageRotation"] = models.ForeignKey(
        StorageRotation,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    from_state: models.CharField[Any, Any] = models.CharField(
        max_length=24,
        choices=StorageRotation.State.choices,
    )
    target_state: models.CharField[Any, Any] = models.CharField(
        max_length=24,
        choices=StorageRotation.State.choices,
    )
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    verification_receipt: models.ForeignKey[
        "StorageRotationVerificationReceipt | None"
    ] = models.ForeignKey(
        StorageRotationVerificationReceipt,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transitions",
    )
    cleanup_receipt: models.ForeignKey["StorageRotationCleanupReceipt | None"] = (
        models.ForeignKey(
            StorageRotationCleanupReceipt,
            null=True,
            blank=True,
            on_delete=models.PROTECT,
            related_name="transitions",
        )
    )
    terminal_failure_reason: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        rotation_id: uuid.UUID
        verification_receipt_id: uuid.UUID | None
        cleanup_receipt_id: uuid.UUID | None

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["rotation", "target_state"],
                name="unique_rotation_target_transition",
            )
        ]


__all__ = [
    "StorageArtifactKind",
    "StorageArtifactPlacement",
    "StorageNodeCapability",
    "StorageNodeState",
    "StorageReservation",
    "StorageReservationTransition",
    "StorageRotation",
    "StorageRotationCleanupReceipt",
    "StorageRotationTransition",
    "StorageRotationVerificationReceipt",
]
