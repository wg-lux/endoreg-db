from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from typing import ClassVar, Literal, TYPE_CHECKING

from django.db import models
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonNull, JsonValue

if TYPE_CHECKING:
    from endoreg_db.models.hub.upload_job import UploadJob
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile


type ReportLlmJobOperation = Literal["report_llm_reimport", "report_llm_import"]
type ReportLlmJobStatus = Literal[
    "queued",
    "running",
    "success",
    "failure",
    "lost",
    "cancelled",
]
type ReportLlmJobJsonValue = (
    JsonValue
    | JsonNull
    | list["ReportLlmJobJsonValue"]
    | dict[str, "ReportLlmJobJsonValue"]
)
type ReportLlmJobJsonObject = dict[str, ReportLlmJobJsonValue]


class ReportLlmInferenceJob(models.Model):
    """Pollable job row for report import/reimport work that may call lx-anonymizer LLMs."""

    objects = models.Manager["ReportLlmInferenceJob"]()

    OPERATION_REIMPORT: Literal["report_llm_reimport"] = "report_llm_reimport"
    OPERATION_IMPORT: Literal["report_llm_import"] = "report_llm_import"
    OPERATION_CHOICES: ClassVar[tuple[tuple[ReportLlmJobOperation, str], ...]] = (
        (OPERATION_REIMPORT, "Report LLM Reimport"),
        (OPERATION_IMPORT, "Report LLM Import"),
    )

    STATUS_QUEUED: Literal["queued"] = "queued"
    STATUS_RUNNING: Literal["running"] = "running"
    STATUS_SUCCESS: Literal["success"] = "success"
    STATUS_FAILURE: Literal["failure"] = "failure"
    STATUS_LOST: Literal["lost"] = "lost"
    STATUS_CANCELLED: Literal["cancelled"] = "cancelled"
    ACTIVE_STATUSES: ClassVar[frozenset[ReportLlmJobStatus]] = frozenset(
        {STATUS_QUEUED, STATUS_RUNNING}
    )
    TERMINAL_STATUSES: ClassVar[frozenset[ReportLlmJobStatus]] = frozenset(
        {STATUS_SUCCESS, STATUS_FAILURE, STATUS_LOST, STATUS_CANCELLED}
    )
    STATUS_CHOICES: ClassVar[tuple[tuple[ReportLlmJobStatus, str], ...]] = (
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILURE, "Failure"),
        (STATUS_LOST, "Lost"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    job_id: models.UUIDField[uuid_lib.UUID] = models.UUIDField(
        default=uuid_lib.uuid4, editable=False, unique=True
    )
    pdf: models.ForeignKey["RawPdfFile | None"] = models.ForeignKey(
        "RawPdfFile",
        on_delete=models.CASCADE,
        related_name="llm_inference_jobs",
        null=True,
        blank=True,
    )
    upload_job: models.ForeignKey["UploadJob | None"] = models.ForeignKey(
        "UploadJob",
        on_delete=models.SET_NULL,
        related_name="report_llm_inference_jobs",
        null=True,
        blank=True,
    )
    operation: models.CharField[str] = models.CharField(
        max_length=64, choices=OPERATION_CHOICES
    )
    status: models.CharField[str] = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        db_index=True,
    )
    task_id: models.CharField[str] = models.CharField(
        max_length=100, blank=True, db_index=True
    )
    queue: models.CharField[str] = models.CharField(max_length=64)
    config: models.JSONField[ReportLlmJobJsonObject] = models.JSONField(
        default=dict, blank=True
    )
    result: models.JSONField[ReportLlmJobJsonObject] = models.JSONField(
        default=dict, blank=True
    )
    error: models.TextField[str] = models.TextField(blank=True)
    created_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)
    started_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        blank=True, null=True
    )
    completed_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        blank=True, null=True
    )

    class Meta:
        db_table = "report_llm_inference_job"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["pdf", "operation", "status"]),
            models.Index(fields=["upload_job", "operation", "status"]),
            models.Index(fields=["queue", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    @property
    def job_key(self) -> str:
        return self.job_id.hex

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def mark_running(self) -> None:
        self.status = self.STATUS_RUNNING
        self.started_at = self.started_at or timezone.now()
        self.save(update_fields=["status", "started_at", "updated_at"])

    def mark_success(self, *, result: ReportLlmJobJsonObject | None = None) -> None:
        self.status = self.STATUS_SUCCESS
        self.completed_at = timezone.now()
        self.error = ""
        if result is not None:
            self.result = result
        self.save(
            update_fields=["status", "completed_at", "error", "result", "updated_at"]
        )

    def mark_failure(self, error: str) -> None:
        self.status = self.STATUS_FAILURE
        self.completed_at = timezone.now()
        self.error = error
        self.save(update_fields=["status", "completed_at", "error", "updated_at"])

    def mark_lost(self, error: str) -> None:
        self.status = self.STATUS_LOST
        self.completed_at = timezone.now()
        self.error = error
        self.save(update_fields=["status", "completed_at", "error", "updated_at"])

    def __str__(self) -> str:
        return f"{self.operation} job={self.job_key} status={self.status}"
