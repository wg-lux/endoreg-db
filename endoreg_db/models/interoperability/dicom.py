from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models

from endoreg_db.schemas.dicom_export import dump_dicom_export_manifest_v2


class DicomExportJob(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        IMPORTED = "imported", "Imported"
        FAILED = "failed", "Failed"

    id: models.UUIDField[uuid.UUID, Any] = models.UUIDField(
        primary_key=True,
        editable=False,
    )
    patient_examination: models.ForeignKey[Any] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.PROTECT,
        related_name="dicom_export_jobs",
    )
    status: models.CharField[str, Any] = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    source_system: models.CharField[str, Any] = models.CharField(max_length=128)
    schema_version: models.PositiveSmallIntegerField[int, Any] = (
        models.PositiveSmallIntegerField(default=2)
    )
    manifest_sha256: models.CharField[str, Any] = models.CharField(
        max_length=64,
        unique=True,
    )
    manifest: models.JSONField[dict[str, object], dict[str, object]] = (
        models.JSONField()
    )
    status_detail: models.TextField[str, Any] = models.TextField(blank=True, default="")
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    if TYPE_CHECKING:
        patient_examination_id: int
        study: DicomStudy

    class Meta:
        ordering = ["-created_at"]

    def clean(self) -> None:
        super().clean()
        try:
            normalized = dump_dicom_export_manifest_v2(self.manifest)
        except ValueError as exc:
            raise ValidationError({"manifest": str(exc)}) from exc
        if str(normalized["export_id"]) != str(self.pk):
            raise ValidationError(
                {"manifest": "manifest export_id must match the export job id"}
            )
        self.manifest = normalized

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)


class DicomStudy(models.Model):
    export_job: models.OneToOneField[DicomExportJob] = models.OneToOneField(
        DicomExportJob,
        on_delete=models.PROTECT,
        related_name="study",
    )
    patient_examination: models.ForeignKey[Any] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.PROTECT,
        related_name="dicom_studies",
    )
    study_instance_uid: models.CharField[str, Any] = models.CharField(
        max_length=64,
        unique=True,
    )
    patient_pseudonym: models.CharField[str, Any] = models.CharField(max_length=255)
    accession_identifier: models.CharField[str | None, Any] = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    study_date: models.DateField[Any, Any] = models.DateField(
        null=True,
        blank=True,
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        series: models.Manager[DicomSeries]

    class Meta:
        ordering = ["study_instance_uid"]
        indexes = [models.Index(fields=["patient_examination", "study_date"])]

    def __str__(self) -> str:
        return self.study_instance_uid


class DicomSeries(models.Model):
    study: models.ForeignKey[DicomStudy] = models.ForeignKey(
        DicomStudy,
        on_delete=models.CASCADE,
        related_name="series",
    )
    series_instance_uid: models.CharField[str, Any] = models.CharField(
        max_length=64,
        unique=True,
    )
    modality: models.CharField[str, Any] = models.CharField(max_length=16)
    series_number: models.PositiveIntegerField[int | None, Any] = (
        models.PositiveIntegerField(null=True, blank=True)
    )

    if TYPE_CHECKING:
        instances: models.Manager[DicomInstance]

    class Meta:
        ordering = ["series_number", "series_instance_uid"]
        indexes = [models.Index(fields=["study", "modality"])]

    def __str__(self) -> str:
        return self.series_instance_uid


class DicomInstance(models.Model):
    class ArtifactClass(models.TextChoices):
        ANONYMIZED_PROCESSED = (
            "anonymized_processed",
            "Anonymized processed",
        )

    series: models.ForeignKey[DicomSeries] = models.ForeignKey(
        DicomSeries,
        on_delete=models.CASCADE,
        related_name="instances",
    )
    sop_instance_uid: models.CharField[str, Any] = models.CharField(
        max_length=64,
        unique=True,
    )
    sop_class_uid: models.CharField[str, Any] = models.CharField(max_length=64)
    transfer_syntax_uid: models.CharField[str, Any] = models.CharField(max_length=64)
    instance_number: models.PositiveIntegerField[int | None, Any] = (
        models.PositiveIntegerField(null=True, blank=True)
    )
    artifact_reference: models.CharField[str, Any] = models.CharField(max_length=1024)
    artifact_class: models.CharField[str, Any] = models.CharField(
        max_length=32,
        choices=ArtifactClass.choices,
    )
    artifact_sha256: models.CharField[str, Any] = models.CharField(max_length=64)
    size_bytes: models.PositiveBigIntegerField[int, Any] = (
        models.PositiveBigIntegerField()
    )
    masked_regions: models.PositiveIntegerField[int, Any] = models.PositiveIntegerField(
        default=0
    )

    class Meta:
        ordering = ["instance_number", "sop_instance_uid"]
        indexes = [
            models.Index(fields=["series", "instance_number"]),
            models.Index(fields=["artifact_sha256"]),
        ]

    def __str__(self) -> str:
        return self.sop_instance_uid
