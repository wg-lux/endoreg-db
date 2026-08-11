from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from django.db import models
from django.db.models import Q

if TYPE_CHECKING:
    from endoreg_db.models.hub.storage_placement import (
        StorageArtifactPlacement,
        StorageRotation,
    )


def _sha256(value: str, *, field_name: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal digest")
    return normalized


class StorageTransferEvidence(models.Model):
    """Persisted correlation between a placement and one encrypted wire object."""

    _allow_state_transition: bool = False

    class State(models.TextChoices):
        STORED = "stored", "Stored"
        VERIFIED = "verified", "Verified"
        RETIRED = "retired", "Retired"
        DELETED = "deleted", "Deleted"
        FAILED = "failed", "Failed"

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    placement: models.ForeignKey["StorageArtifactPlacement"] = models.ForeignKey(
        "StorageArtifactPlacement",
        on_delete=models.PROTECT,
        related_name="transfer_evidence",
    )
    rotation: models.OneToOneField["StorageRotation | None"] = models.OneToOneField(
        "StorageRotation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="target_transfer_evidence",
    )
    envelope_generation: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    state: models.CharField[Any, Any] = models.CharField(
        max_length=16, choices=State.choices, default=State.STORED
    )
    node_key: models.CharField[Any, Any] = models.CharField(max_length=253)
    artifact_kind: models.CharField[Any, Any] = models.CharField(max_length=32)
    envelope_profile: models.CharField[Any, Any] = models.CharField(max_length=64)
    recipient_key_id: models.CharField[Any, Any] = models.CharField(max_length=64)
    plaintext_sha256: models.CharField[Any, Any] = models.CharField(max_length=64)
    plaintext_size: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    ciphertext_sha256: models.CharField[Any, Any] = models.CharField(max_length=64)
    ciphertext_size: models.PositiveBigIntegerField[Any, Any] = (
        models.PositiveBigIntegerField()
    )
    store_idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255, unique=True
    )
    store_request_fingerprint: models.CharField[Any, Any] = models.CharField(
        max_length=64
    )
    verify_idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255, unique=True, null=True, blank=True
    )
    verify_request_fingerprint: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    retire_idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255, unique=True, null=True, blank=True
    )
    retire_request_fingerprint: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    delete_idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255, unique=True, null=True, blank=True
    )
    delete_request_fingerprint: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    verifier: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    verification_reference: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    stored_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    verified_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    retired_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    deleted_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        null=True, blank=True
    )
    failure_reason: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        placement_id: uuid.UUID
        rotation_id: uuid.UUID | None

    class Meta:
        ordering = ["placement_id", "envelope_generation", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(envelope_generation__gt=0),
                name="storage_transfer_generation_positive",
            ),
            models.CheckConstraint(
                condition=Q(plaintext_size__gt=0) & Q(ciphertext_size__gt=0),
                name="storage_transfer_sizes_positive",
            ),
            models.UniqueConstraint(
                fields=["placement", "envelope_generation"],
                name="unique_placement_envelope_generation",
            ),
            models.UniqueConstraint(
                fields=["placement"],
                condition=Q(state="verified"),
                name="unique_verified_placement_envelope",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state="stored",
                        verified_at__isnull=True,
                        retired_at__isnull=True,
                        deleted_at__isnull=True,
                        verify_idempotency_key__isnull=True,
                        verify_request_fingerprint="",
                        verifier="",
                        verification_reference="",
                        failure_reason="",
                    )
                    | Q(
                        state="verified",
                        verified_at__isnull=False,
                        retired_at__isnull=True,
                        deleted_at__isnull=True,
                        verify_idempotency_key__isnull=False,
                    )
                    | Q(
                        state="retired",
                        verified_at__isnull=False,
                        retired_at__isnull=False,
                        retire_idempotency_key__isnull=False,
                    )
                    | Q(
                        state="deleted",
                        deleted_at__isnull=False,
                        delete_idempotency_key__isnull=False,
                    )
                    | (Q(state="failed") & ~Q(failure_reason=""))
                ),
                name="storage_transfer_state_evidence_consistent",
            ),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        self.recipient_key_id = _sha256(
            self.recipient_key_id, field_name="recipient_key_id"
        )
        self.plaintext_sha256 = _sha256(
            self.plaintext_sha256, field_name="plaintext_sha256"
        )
        self.ciphertext_sha256 = _sha256(
            self.ciphertext_sha256, field_name="ciphertext_sha256"
        )
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("state", flat=True)
                .first()
            )
            if (
                previous is not None
                and previous != self.state
                and not self._allow_state_transition
            ):
                raise ValueError(
                    "transfer evidence state changes require the lifecycle service"
                )
        super().save(*args, **kwargs)

    def apply_verified(
        self,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        verifier: str,
        reference: str,
        verified_at: datetime,
    ) -> None:
        self.state = self.State.VERIFIED
        self.verify_idempotency_key = idempotency_key
        self.verify_request_fingerprint = request_fingerprint
        self.verifier = verifier
        self.verification_reference = reference
        self.verified_at = verified_at
        self._allow_state_transition = True
        try:
            self.save(
                update_fields=[
                    "state",
                    "verify_idempotency_key",
                    "verify_request_fingerprint",
                    "verifier",
                    "verification_reference",
                    "verified_at",
                    "updated_at",
                ]
            )
        finally:
            self._allow_state_transition = False

    def apply_retired(
        self,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        retired_at: datetime,
    ) -> None:
        self.state = self.State.RETIRED
        self.retire_idempotency_key = idempotency_key
        self.retire_request_fingerprint = request_fingerprint
        self.retired_at = retired_at
        self._allow_state_transition = True
        try:
            self.save(
                update_fields=[
                    "state",
                    "retire_idempotency_key",
                    "retire_request_fingerprint",
                    "retired_at",
                    "updated_at",
                ]
            )
        finally:
            self._allow_state_transition = False

    def apply_deleted(
        self,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        deleted_at: datetime,
    ) -> None:
        self.state = self.State.DELETED
        self.delete_idempotency_key = idempotency_key
        self.delete_request_fingerprint = request_fingerprint
        self.deleted_at = deleted_at
        self._allow_state_transition = True
        try:
            self.save(
                update_fields=[
                    "state",
                    "delete_idempotency_key",
                    "delete_request_fingerprint",
                    "deleted_at",
                    "updated_at",
                ]
            )
        finally:
            self._allow_state_transition = False

    def apply_failed(self, *, reason: str) -> None:
        normalized = reason.strip()
        if not normalized:
            raise ValueError("transfer failure reason must not be blank")
        self.state = self.State.FAILED
        self.failure_reason = normalized[:255]
        self._allow_state_transition = True
        try:
            self.save(update_fields=["state", "failure_reason", "updated_at"])
        finally:
            self._allow_state_transition = False


class StoragePlacementCommitReceipt(models.Model):
    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    placement: models.OneToOneField["StorageArtifactPlacement"] = models.OneToOneField(
        "StorageArtifactPlacement",
        on_delete=models.PROTECT,
        related_name="initial_commit_receipt",
    )
    transfer_evidence: models.OneToOneField["StorageTransferEvidence"] = (
        models.OneToOneField(
            StorageTransferEvidence,
            on_delete=models.PROTECT,
            related_name="placement_commit_receipt",
        )
    )
    idempotency_key: models.CharField[Any, Any] = models.CharField(
        max_length=255, unique=True
    )
    request_fingerprint: models.CharField[Any, Any] = models.CharField(max_length=64)
    committed_at: models.DateTimeField[Any, Any] = models.DateTimeField()
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        placement_id: uuid.UUID
        transfer_evidence_id: uuid.UUID

    class Meta:
        ordering = ["created_at", "pk"]


__all__ = ["StoragePlacementCommitReceipt", "StorageTransferEvidence"]
