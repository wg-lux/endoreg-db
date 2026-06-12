from __future__ import annotations

from datetime import datetime
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias
import uuid

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.schemas import (
    validate_transfer_processing_snapshot,
    validate_transfer_provenance_payload,
    validate_transfer_resource_rows,
)

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center import Center
    from .network_node import NetworkNode
    from .upload_job import UploadJob

NoTransferJobRelationValue: TypeAlias = NoneType
NoTransferJobIntegerValue: TypeAlias = NoneType
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
        AWAITING_MEDIA = "awaiting_media", "Awaiting Media"
        APPLIED = "applied", "Applied"
        FAILED = "failed", "Failed"
        INCONSISTENT = "inconsistent", "Inconsistent"

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

    id: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    transfer_key: models.CharField[str, str] = models.CharField(
        max_length=255, unique=True, db_index=True
    )
    source_node: models.ForeignKey[NetworkNode, NetworkNode] = models.ForeignKey(
        "NetworkNode",
        on_delete=models.PROTECT,
        related_name="sent_transfer_jobs",
    )
    target_node: models.ForeignKey[NetworkNode, NetworkNode] = models.ForeignKey(
        "NetworkNode",
        on_delete=models.PROTECT,
        related_name="received_transfer_jobs",
    )
    source_center: models.ForeignKey[TransferJobCenter, TransferJobCenter] = (
        models.ForeignKey(
            "Center",
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name="transfer_jobs",
        )
    )
    resource_kind: models.CharField[str, str] = models.CharField(
        max_length=16, choices=ResourceKind.choices
    )
    resource_hash: models.CharField[str, str] = models.CharField(
        max_length=255, db_index=True
    )
    transfer_mode: models.CharField[str, str] = models.CharField(
        max_length=48,
        choices=TransferMode.choices,
        default=TransferMode.METADATA_ONLY,
    )
    transfer_status: models.CharField[str, str] = models.CharField(
        max_length=32,
        choices=TransferStatus.choices,
        default=TransferStatus.PENDING,
    )
    processing_policy: models.CharField[str, str] = models.CharField(
        max_length=48,
        choices=ProcessingPolicy.choices,
        default=ProcessingPolicy.PRESERVE_PROCESSING_STATE,
    )
    processing_intent: models.CharField[str, str] = models.CharField(
        max_length=48,
        choices=ProcessingIntent.choices,
        default=ProcessingIntent.STATE_PRESERVATION,
    )
    processing_decision: models.CharField[str, str] = models.CharField(
        max_length=48,
        choices=ProcessingDecision.choices,
        default=ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
    )
    cleanup_policy: models.CharField[str, str] = models.CharField(
        max_length=64,
        choices=CleanupPolicy.choices,
        default=CleanupPolicy.RETAIN_ALL,
    )
    cleanup_status: models.CharField[str, str] = models.CharField(
        max_length=32,
        choices=CleanupStatus.choices,
        default=CleanupStatus.PENDING,
    )
    payload_schema_version: models.CharField[str, str] = models.CharField(
        max_length=32, default="1.0"
    )
    resource_rows: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    processing_snapshot: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    status_detail: models.TextField[str, str] = models.TextField(blank=True, default="")
    provenance: models.JSONField[JsonObject, JsonObject] = models.JSONField(
        default=dict, blank=True
    )
    target_object_id: models.PositiveBigIntegerField[
        TransferJobInteger, TransferJobInteger
    ] = models.PositiveBigIntegerField(null=True, blank=True)
    linked_patient_id: models.PositiveBigIntegerField[
        TransferJobInteger, TransferJobInteger
    ] = models.PositiveBigIntegerField(null=True, blank=True)
    linked_patient_examination_id: models.PositiveBigIntegerField[
        TransferJobInteger, TransferJobInteger
    ] = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )
    case_resolution_status: models.CharField[str, str] = models.CharField(
        max_length=24,
        choices=CaseResolutionStatus.choices,
        default=CaseResolutionStatus.PENDING,
    )
    upload_job: models.ForeignKey[TransferJobUploadJob, TransferJobUploadJob] = (
        models.ForeignKey(
            "UploadJob",
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
            related_name="transfer_jobs",
        )
    )
    created_by: models.ForeignKey[TransferJobUser, TransferJobUser] = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_transfer_jobs",
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = ["-created_at"]

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

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)
