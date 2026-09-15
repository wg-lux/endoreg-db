from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, TypeAlias, Unpack
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.schemas import (
    validate_transfer_processing_snapshot,
    validate_transfer_provenance_payload,
    validate_transfer_resource_rows,
)

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center import Center
    from .upload_job import UploadJob

NoTransferJobRelationValue: TypeAlias = None
NoTransferJobIntegerValue: TypeAlias = None
TransferJobCenter: TypeAlias = "Center | NoTransferJobRelationValue"
TransferJobUploadJob: TypeAlias = "UploadJob | NoTransferJobRelationValue"
TransferJobUser: TypeAlias = "User | NoTransferJobRelationValue"
TransferJobInteger: TypeAlias = "int | NoTransferJobIntegerValue"


class TransferJob(models.Model):
    class ResourceKind(models.TextChoices):
        VIDEO = "video", "Video"
        REPORT = "report", "Report"

    class TransferMode(models.TextChoices):
        METADATA_ONLY = "metadata_only", "Metadata Only"
        METADATA_AND_RAW_MEDIA = "metadata_and_raw_media", "Metadata And Raw Media"
        METADATA_AND_PROCESSED_MEDIA = (
            "metadata_and_processed_media",
            "Metadata And Processed Media",
        )
        METADATA_RAW_AND_PROCESSED_MEDIA = (
            "metadata_raw_and_processed_media",
            "Metadata Raw And Processed Media",
        )

    class TransferStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CLAIMED = "claimed", "Claimed"
        RUNNING = "running", "Running"
        RETRY_WAIT = "retry_wait", "Retry wait"
        AWAITING_MEDIA = "awaiting_media", "Awaiting Media"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"
        INCONSISTENT = "inconsistent", "Inconsistent"
        LOST = "lost", "Lost"

    class ProcessingPolicy(models.TextChoices):
        REPROCESS_ALWAYS = "reprocess_always", "Reprocess Always"
        REPROCESS_IF_MISSING_OUTPUTS = (
            "reprocess_if_missing_outputs",
            "Reprocess If Missing Outputs",
        )
        PRESERVE_PROCESSING_STATE = (
            "preserve_processing_state",
            "Preserve Processing State",
        )
        INGEST_ONLY_NO_PROCESSING = (
            "ingest_only_no_processing",
            "Ingest Only No Processing",
        )

    class ProcessingIntent(models.TextChoices):
        HUB_PROCESSING = (
            "sender_requests_hub_processing",
            "Sender Requests Hub Processing",
        )
        STATE_PRESERVATION = (
            "sender_requests_state_preservation",
            "Sender Requests State Preservation",
        )
        ARCHIVE_ONLY = "sender_requests_archive_only", "Sender Requests Archive Only"

    class ProcessingDecision(models.TextChoices):
        START_PROCESSING = "start_processing", "Start Processing"
        SKIP_EXISTING_SUCCESS = (
            "skip_processing_existing_success",
            "Skip Processing Existing Success",
        )
        SKIP_PRESERVED_STATE = (
            "skip_processing_preserved_state",
            "Skip Processing Preserved State",
        )
        WAIT_FOR_MISSING_MEDIA = "wait_for_missing_media", "Wait For Missing Media"
        MARK_INCONSISTENT = "mark_inconsistent", "Mark Inconsistent"
        REJECT_TRANSFER = "reject_transfer", "Reject Transfer"

    class CleanupPolicy(models.TextChoices):
        RETAIN_ALL = "retain_all", "Retain All"
        DELETE_CENTRAL_RAW_AFTER_APPLY = (
            "delete_central_raw_after_apply",
            "Delete Central Raw After Apply",
        )
        DELETE_CENTRAL_RAW_AFTER_VERIFIED_BACKUP = (
            "delete_central_raw_after_verified_backup",
            "Delete Central Raw After Verified Backup",
        )
        DELETE_CENTRAL_SOURCE_AFTER_ANONYMIZED_DERIVATIVES_EXIST = (
            "delete_central_source_after_anonymized_derivatives_exist",
            "Delete Central Source After Anonymized Derivatives Exist",
        )

    class CleanupStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        NOT_REQUESTED = "not_requested", "Not Requested"
        DEFERRED = "deferred", "Deferred"
        COMPLETED = "completed", "Completed"

    class CaseResolutionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        LINKED = "linked", "Linked"
        AMBIGUOUS = "ambiguous", "Ambiguous"
        UNRESOLVED = "unresolved", "Unresolved"

    id: models.UUIDField[uuid.UUID, Any] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    transfer_key: models.CharField[str, Any] = models.CharField(
        max_length=255, unique=True, db_index=True
    )
    source_node: models.ForeignKey[Any] = models.ForeignKey(
        "NetworkNode",
        on_delete=models.PROTECT,
        related_name="sent_transfer_jobs",
    )
    target_node: models.ForeignKey[Any] = models.ForeignKey(
        "NetworkNode",
        on_delete=models.PROTECT,
        related_name="received_transfer_jobs",
    )
    source_center: models.ForeignKey[Any] = models.ForeignKey(
        "Center",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transfer_jobs",
    )
    resource_kind: models.CharField[str, Any] = models.CharField(
        max_length=16, choices=ResourceKind.choices
    )
    resource_hash: models.CharField[str, Any] = models.CharField(
        max_length=255, db_index=True
    )
    transfer_mode: models.CharField[str, Any] = models.CharField(
        max_length=48,
        choices=TransferMode.choices,
        default=TransferMode.METADATA_ONLY,
    )
    transfer_status: models.CharField[str, Any] = models.CharField(
        max_length=32,
        choices=TransferStatus.choices,
        default=TransferStatus.PENDING,
    )
    attempt_id: models.UUIDField[uuid.UUID | None, Any] = models.UUIDField(
        null=True, blank=True, editable=False
    )
    operation_owner: models.CharField[str, Any] = models.CharField(
        max_length=255, blank=True, default="", editable=False
    )
    operation_fencing_token: models.PositiveBigIntegerField[int, Any] = (
        models.PositiveBigIntegerField(default=0, editable=False)
    )
    operation_heartbeat_at: models.DateTimeField[datetime | None, Any] = (
        models.DateTimeField(null=True, blank=True, editable=False)
    )
    operation_lease_expires_at: models.DateTimeField[datetime | None, Any] = (
        models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    )
    operation_candidate_name: models.CharField[str, Any] = models.CharField(
        max_length=1024, blank=True, default="", editable=False
    )
    processing_policy: models.CharField[str, Any] = models.CharField(
        max_length=48,
        choices=ProcessingPolicy.choices,
        default=ProcessingPolicy.PRESERVE_PROCESSING_STATE,
    )
    processing_intent: models.CharField[str, Any] = models.CharField(
        max_length=48,
        choices=ProcessingIntent.choices,
        default=ProcessingIntent.STATE_PRESERVATION,
    )
    processing_decision: models.CharField[str, Any] = models.CharField(
        max_length=48,
        choices=ProcessingDecision.choices,
        default=ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
    )
    cleanup_policy: models.CharField[str, Any] = models.CharField(
        max_length=64,
        choices=CleanupPolicy.choices,
        default=CleanupPolicy.RETAIN_ALL,
    )
    cleanup_status: models.CharField[str, Any] = models.CharField(
        max_length=32,
        choices=CleanupStatus.choices,
        default=CleanupStatus.PENDING,
    )
    payload_schema_version: models.CharField[str, Any] = models.CharField(
        max_length=32, default="1.0"
    )
    resource_rows: models.JSONField[Any, Any] = models.JSONField(
        default=dict, blank=True
    )
    processing_snapshot: models.JSONField[Any, Any] = models.JSONField(
        default=dict, blank=True
    )
    status_detail: models.TextField[str, Any] = models.TextField(blank=True, default="")
    provenance: models.JSONField[Any, Any] = models.JSONField(default=dict, blank=True)
    target_object_id: models.PositiveBigIntegerField[TransferJobInteger | None, Any] = (
        models.PositiveBigIntegerField(null=True, blank=True)
    )
    linked_patient_id: models.PositiveBigIntegerField[
        TransferJobInteger | None, Any
    ] = models.PositiveBigIntegerField(null=True, blank=True)
    linked_patient_examination_id: models.PositiveBigIntegerField[
        TransferJobInteger | None, Any
    ] = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )
    case_resolution_status: models.CharField[str, Any] = models.CharField(
        max_length=24,
        choices=CaseResolutionStatus.choices,
        default=CaseResolutionStatus.PENDING,
    )
    upload_job: models.ForeignKey[Any] = models.ForeignKey(
        "UploadJob",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transfer_jobs",
    )
    created_by: models.ForeignKey[Any] = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_transfer_jobs",
    )
    created_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, Any] = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        transfer_status="running",
                        attempt_id__isnull=False,
                        operation_heartbeat_at__isnull=False,
                        operation_lease_expires_at__isnull=False,
                    )
                    & ~models.Q(operation_owner="")
                )
                | (
                    ~models.Q(transfer_status="running")
                    & models.Q(
                        attempt_id__isnull=True,
                        operation_owner="",
                        operation_heartbeat_at__isnull=True,
                        operation_lease_expires_at__isnull=True,
                    )
                ),
                name="transfer_operation_lease_consistent",
            )
        ]

    def __str__(self) -> str:
        return f"{self.transfer_key} ({self.transfer_status})"

    def clean(self) -> None:
        super().clean()
        try:
            self.resource_rows = validate_transfer_resource_rows(
                self.resource_rows,
                resource_kind=self.resource_kind,
            )
        except ValueError as exc:
            raise ValidationError({"resource_rows": str(exc)}) from exc
        try:
            self.processing_snapshot = validate_transfer_processing_snapshot(
                self.processing_snapshot
            )
        except ValueError as exc:
            raise ValidationError({"processing_snapshot": str(exc)}) from exc
        try:
            self.provenance = validate_transfer_provenance_payload(self.provenance)
        except ValueError as exc:
            raise ValidationError({"provenance": str(exc)}) from exc

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.clean()
        super().save(*args, **kwargs)
