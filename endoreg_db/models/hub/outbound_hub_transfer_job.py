from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Unpack
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.hub.network_node import NetworkNode
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.media.video.video_file import VideoFile


class OutboundHubTransferJob(models.Model):
    objects = models.Manager["OutboundHubTransferJob"]()

    if TYPE_CHECKING:
        video_file_id: int | None
        raw_pdf_file_id: int | None
        source_center_id: int | None
        target_node_id: int
        marked_by_id: int | None

    class ResourceKind(models.TextChoices):
        VIDEO = "video", "Video"
        REPORT = "report", "Report"

    class LocalStatus(models.TextChoices):
        MARKED = "marked", "Marked"
        QUEUED = "queued", "Queued"
        REGISTERING = "registering", "Registering"
        AWAITING_MEDIA = "awaiting_media", "Awaiting Media"
        UPLOADING = "uploading", "Uploading"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class FailureClass(models.TextChoices):
        NO_FAILURE = "", "No Failure"
        CONFIGURATION_REJECTION = (
            "configuration_rejection",
            "Configuration Rejection",
        )
        AUTHORIZATION_DENIAL = "authorization_denial", "Authorization Denial"
        INTEGRITY_INCONSISTENCY = (
            "integrity_inconsistency",
            "Integrity Inconsistency",
        )
        TRANSIENT_RETRY = "transient_retry", "Transient Retry"

    class TransferMode(models.TextChoices):
        METADATA_AND_PROCESSED_MEDIA = (
            "metadata_and_processed_media",
            "Metadata And Processed Media",
        )

    class LocalCleanupPolicy(models.TextChoices):
        RETAIN_PROCESSED_MEDIA = (
            "retain_processed_media",
            "Retain Processed Media",
        )
        ELIGIBLE_AFTER_VERIFIED_APPLY = (
            "eligible_after_verified_apply",
            "Eligible After Verified Apply",
        )

    class LocalCleanupStatus(models.TextChoices):
        NOT_APPLICABLE = "not_applicable", "Not Applicable"
        RETAINED = "retained", "Retained"
        ELIGIBLE = "eligible", "Eligible"
        CLEANING = "cleaning", "Cleaning"
        CLEANED = "cleaned", "Cleaned"

    id: models.UUIDField[UUID, UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    resource_kind: models.CharField[str, str] = models.CharField(
        max_length=16, choices=ResourceKind.choices
    )
    video_file: models.ForeignKey[VideoFile | None] = models.ForeignKey(
        "endoreg_db.VideoFile",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="outbound_hub_transfer_jobs",
    )
    raw_pdf_file: models.ForeignKey[RawPdfFile | None] = models.ForeignKey(
        "endoreg_db.RawPdfFile",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="outbound_hub_transfer_jobs",
    )
    source_center: models.ForeignKey[Center | None] = models.ForeignKey(
        "endoreg_db.Center",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outbound_hub_transfer_jobs",
    )
    target_node: models.ForeignKey[NetworkNode] = models.ForeignKey(
        "endoreg_db.NetworkNode",
        on_delete=models.PROTECT,
        related_name="outbound_hub_transfer_jobs",
    )
    transfer_key: models.CharField[str, str] = models.CharField(
        max_length=255, unique=True, db_index=True
    )
    transfer_mode: models.CharField[str, str] = models.CharField(
        max_length=48,
        choices=TransferMode.choices,
        default=TransferMode.METADATA_AND_PROCESSED_MEDIA,
    )
    local_cleanup_policy: models.CharField[str, str] = models.CharField(
        max_length=48,
        choices=LocalCleanupPolicy.choices,
        default=LocalCleanupPolicy.RETAIN_PROCESSED_MEDIA,
    )
    local_cleanup_status: models.CharField[str, str] = models.CharField(
        max_length=32,
        choices=LocalCleanupStatus.choices,
        default=LocalCleanupStatus.NOT_APPLICABLE,
    )
    local_cleanup_eligible_at: models.DateTimeField[
        datetime | None, datetime | None
    ] = models.DateTimeField(null=True, blank=True)
    local_cleanup_completed_at: models.DateTimeField[
        datetime | None, datetime | None
    ] = models.DateTimeField(null=True, blank=True)
    envelope_receipt: models.JSONField[JsonObject | None, JsonObject | None] = (
        models.JSONField(null=True, blank=True)
    )
    local_status: models.CharField[str, str] = models.CharField(
        max_length=32,
        choices=LocalStatus.choices,
        default=LocalStatus.MARKED,
        db_index=True,
    )
    retry_count: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(
        default=0
    )
    failure_class: models.CharField[str, str] = models.CharField(
        max_length=32,
        choices=FailureClass.choices,
        blank=True,
        default=FailureClass.NO_FAILURE,
        db_index=True,
    )
    last_error: models.TextField[str, str] = models.TextField(blank=True, default="")
    remote_transfer_id: models.CharField[str, str] = models.CharField(
        max_length=64, blank=True, default=""
    )
    remote_transfer_status: models.CharField[str, str] = models.CharField(
        max_length=32,
        blank=True,
        default="",
    )
    remote_processing_decision: models.CharField[str, str] = models.CharField(
        max_length=48,
        blank=True,
        default="",
    )
    marked_by: models.ForeignKey[User | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="marked_outbound_hub_transfer_jobs",
    )
    marked_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    queued_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    registration_started_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    media_upload_started_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    last_attempt_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    completed_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["video_file", "target_node", "transfer_mode"],
                name="endoreg_outbound_hub_transfer_unique_video_target_mode",
            ),
            models.UniqueConstraint(
                fields=["raw_pdf_file", "target_node", "transfer_mode"],
                name="endoreg_outbound_hub_transfer_unique_report_target_mode",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.transfer_key} ({self.local_status})"

    def clean(self) -> None:
        super().clean()

        has_video = self.video_file_id is not None
        has_report = self.raw_pdf_file_id is not None
        if has_video == has_report:
            raise ValidationError(
                "Exactly one of video_file or raw_pdf_file must be set.",
            )

        if self.resource_kind == str(self.ResourceKind.VIDEO):
            if not has_video:
                raise ValidationError(
                    {
                        "video_file": (
                            "video_file is required when resource_kind='video'."
                        ),
                    },
                )
            if has_report:
                raise ValidationError(
                    {
                        "raw_pdf_file": (
                            "raw_pdf_file must be empty for video transfers."
                        ),
                    },
                )

        if self.resource_kind == str(self.ResourceKind.REPORT):
            if not has_report:
                raise ValidationError(
                    {
                        "raw_pdf_file": (
                            "raw_pdf_file is required when resource_kind='report'."
                        ),
                    },
                )
            if has_video:
                raise ValidationError(
                    {"video_file": ("video_file must be empty for report transfers.")},
                )

        if self.target_node.role != "central_hub":
            raise ValidationError(
                {"target_node": "target_node must have role='central_hub'."},
            )

        if self.transfer_mode != str(self.TransferMode.METADATA_AND_PROCESSED_MEDIA):
            raise ValidationError(
                {
                    "transfer_mode": (
                        "Only metadata_and_processed_media is permitted for "
                        "outbound hub transfer."
                    ),
                },
            )

        if self.envelope_receipt is not None:
            from lx_dtypes.models.contracts.hub_media_envelope import (
                HubMediaEnvelopeReceipt,
            )

            try:
                receipt = HubMediaEnvelopeReceipt.model_validate(self.envelope_receipt)
            except ValueError as exc:
                raise ValidationError(
                    {"envelope_receipt": "Envelope receipt is invalid."},
                ) from exc

            resource = self.video_file if has_video else self.raw_pdf_file
            resource_hash_field = "raw_video_hash" if has_video else "pdf_hash"
            processed_hash = (
                str(getattr(resource, "processed_video_hash", "") or "").strip()
                if has_video
                else str(
                    getattr(
                        getattr(resource, "state", None),
                        "processed_file_sha256",
                        "",
                    )
                    or "",
                ).strip()
            )
            expected = {
                "transfer_key": str(self.transfer_key),
                "target_node_key": str(self.target_node.node_key),
                "source_center_key": str(
                    getattr(self.source_center, "center_key", "") or "",
                ),
                "resource_kind": str(self.resource_kind),
                "resource_hash": str(
                    getattr(resource, resource_hash_field, "") or "",
                ),
                "processed_media_hash": processed_hash,
            }
            mismatches = [
                field_name
                for field_name, expected_value in expected.items()
                if not expected_value
                or str(getattr(receipt, field_name)) != expected_value
            ]
            if self.remote_transfer_id and (
                receipt.receiver_transfer_id != str(self.remote_transfer_id)
            ):
                mismatches.append("receiver_transfer_id")
            if mismatches:
                raise ValidationError(
                    {
                        "envelope_receipt": (
                            "Envelope receipt does not match the outbound job: "
                            + ", ".join(sorted(set(mismatches)))
                        ),
                    },
                )

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.full_clean()
        super().save(**kwargs)
