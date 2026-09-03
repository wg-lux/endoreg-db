from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, Any

from django.conf import settings
from django.db import models
from django.utils import timezone

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

NoMetricRelationValue: TypeAlias = None
NoMetricFloatValue: TypeAlias = None
MetricVideo: TypeAlias = "VideoFile | NoMetricRelationValue"
MetricPdf: TypeAlias = "RawPdfFile | NoMetricRelationValue"
MetricSensitiveMeta: TypeAlias = "SensitiveMeta | NoMetricRelationValue"
MetricCenter: TypeAlias = "Center | NoMetricRelationValue"
MetricValidatorUser: TypeAlias = "AbstractBaseUser | NoMetricRelationValue"
MetricFloat: TypeAlias = "float | NoMetricFloatValue"


class AnonymizationMetricMediaType(models.TextChoices):
    VIDEO = "video", "Video"
    PDF = "pdf", "PDF"


class AnonymizationMetricField(models.TextChoices):
    PATIENT_FIRST_NAME = "patient_first_name", "Patient First Name"
    PATIENT_LAST_NAME = "patient_last_name", "Patient Last Name"
    PATIENT_DOB = "patient_dob", "Patient Date Of Birth"
    PATIENT_GENDER = "patient_gender", "Patient Gender"
    EXAMINATION_DATE = "examination_date", "Examination Date"
    CASENUMBER = "casenumber", "Case Number"
    CENTER_NAME = "center_name", "Center Name"
    EXTERNAL_ID = "external_id", "External ID"
    DOCUMENT_TYPE = "document_type", "Document Type"


class AnonymizationValidationMetric(models.Model):
    """
    Derived-only metric row for one anonymization validation event.

    This model intentionally stores no raw patient values, report text, OCR text,
    file paths, or reversible value hashes.
    """

    schema_version: models.CharField[Any, Any] = models.CharField(
        max_length=16, default="1.0", editable=False
    )
    media_type: models.CharField[Any, Any] = models.CharField(
        max_length=16,
        choices=AnonymizationMetricMediaType.choices,
        db_index=True,
    )
    video: models.ForeignKey[MetricVideo] = models.ForeignKey(
        "VideoFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anonymization_validation_metrics",
    )
    pdf: models.ForeignKey[MetricPdf] = models.ForeignKey(
        "RawPdfFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anonymization_validation_metrics",
    )
    sensitive_meta: models.ForeignKey[MetricSensitiveMeta] = models.ForeignKey(
        "SensitiveMeta",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anonymization_validation_metrics",
    )
    center: models.ForeignKey[MetricCenter] = models.ForeignKey(
        "Center",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anonymization_validation_metrics",
    )
    validator_user: models.ForeignKey[MetricValidatorUser] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anonymization_validation_metrics",
    )
    validator_username: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    validated_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        default=timezone.now, db_index=True
    )
    status_before: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    status_after: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    document_type: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    source_system: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    anonymizer_source: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="lx_anonymizer",
    )
    anonymizer_version: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    no_more_names_confirmed: models.BooleanField[Any, Any] = models.BooleanField(
        null=True, blank=True
    )
    seconds_to_validation: models.FloatField[Any, Any] = models.FloatField(
        null=True, blank=True
    )
    total_fields: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField(
        default=0
    )
    changed_fields: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField(
        default=0
    )
    exact_match_fields: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField(default=0)
    )
    missing_after_validation_fields: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField(default=0)
    )
    mean_similarity: models.FloatField[Any, Any] = models.FloatField(
        null=True, blank=True
    )
    residual_ocr_match_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField(default=0)
    )
    phi_region_false_negative_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField(default=0)
    )
    raw_artifact_residual_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField(default=0)
    )
    missing_sensitive_meta_deletion_count: models.PositiveIntegerField[Any, Any] = (
        models.PositiveIntegerField(default=0)
    )
    residual_phi_detected: models.BooleanField[Any, Any] = models.BooleanField(
        default=False
    )
    sensitive_meta_policy: models.CharField[Any, Any] = models.CharField(
        max_length=64, blank=True, default=""
    )
    sensitive_meta_deletion_status: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["media_type", "validated_at"],
                name="anon_val_media_time_idx",
            ),
            models.Index(
                fields=["center", "validated_at"],
                name="anon_val_center_time_idx",
            ),
            models.Index(
                fields=["document_type", "validated_at"],
                name="anon_val_doc_time_idx",
            ),
            models.Index(
                fields=["source_system", "validated_at"],
                name="anon_val_source_time_idx",
            ),
        ]
        verbose_name = "Anonymization Validation Metric"
        verbose_name_plural = "Anonymization Validation Metrics"

    def __str__(self) -> str:
        return (
            f"AnonymizationValidationMetric({self.media_type}, "
            f"{self.validated_at.isoformat()}, fields={self.total_fields})"
        )


class AnonymizationFieldMetric(models.Model):
    """
    Derived-only per-field comparison for one validation event.

    Field values are compared transiently in memory by the service layer and are
    never persisted here.
    """

    validation_metric: models.ForeignKey["AnonymizationValidationMetric"] = (
        models.ForeignKey(
            AnonymizationValidationMetric,
            on_delete=models.CASCADE,
            related_name="field_metrics",
        )
    )
    field_name: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        choices=AnonymizationMetricField.choices,
        db_index=True,
    )
    present_before: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    present_after: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    changed: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    exact_match: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    similarity_score: models.FloatField[Any, Any] = models.FloatField(
        null=True, blank=True
    )
    was_required: models.BooleanField[Any, Any] = models.BooleanField(default=False)
    was_empty_after_validation: models.BooleanField[Any, Any] = models.BooleanField(
        default=False
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)

    if TYPE_CHECKING:
        validation_metric_id: int

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["validation_metric", "field_name"],
                name="uniq_anonymization_field_metric_per_validation",
            )
        ]
        indexes = [
            models.Index(
                fields=["field_name", "created_at"],
                name="anon_field_name_created_idx",
            ),
        ]
        verbose_name = "Anonymization Field Metric"
        verbose_name_plural = "Anonymization Field Metrics"

    def __str__(self) -> str:
        return f"{self.validation_metric_id}:{self.field_name}"
