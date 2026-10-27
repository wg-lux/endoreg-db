"""Report-only semantic evidence linked to persisted clinical annotations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Unpack
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.text_embedding import TextEmbedding
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.medical.patient.patient_examination import PatientExamination
    from endoreg_db.models.medical.patient.patient_finding import PatientFinding
    from endoreg_db.models.medical.patient.patient_finding_classification import (
        PatientFindingClassification,
    )


class ReportConceptEmbedding(models.Model):
    report: models.ForeignKey[RawPdfFile] = models.ForeignKey(
        "RawPdfFile", on_delete=models.CASCADE
    )
    examination_annotation: models.ForeignKey[PatientExamination | None] = (
        models.ForeignKey(
            "PatientExamination", on_delete=models.CASCADE, null=True, blank=True
        )
    )
    finding_annotation: models.ForeignKey[PatientFinding | None] = models.ForeignKey(
        "PatientFinding", on_delete=models.CASCADE, null=True, blank=True
    )
    classification_annotation: models.ForeignKey[
        PatientFindingClassification | None
    ] = models.ForeignKey(
        "PatientFindingClassification", on_delete=models.CASCADE, null=True, blank=True
    )
    identity: models.CharField[str, str] = models.CharField(max_length=64, unique=True)
    source_hash: models.CharField[str, str] = models.CharField(max_length=64)
    start: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    end: models.PositiveIntegerField[int, int] = models.PositiveIntegerField()
    phrase: models.TextField[str, str] = models.TextField()
    concept_name: models.CharField[str, str] = models.CharField(max_length=255)
    choice_name: models.CharField[str, str] = models.CharField(
        max_length=255, blank=True
    )
    knowledge_base_module: models.CharField[str, str] = models.CharField(
        max_length=255, blank=True
    )
    knowledge_base_version: models.CharField[str, str] = models.CharField(
        max_length=255, blank=True
    )
    space_key: models.CharField[str, str] = models.CharField(
        max_length=64, db_index=True
    )
    embedding: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    if TYPE_CHECKING:
        report_id: int
        examination_annotation_id: int | None
        finding_annotation_id: int | None
        classification_annotation_id: int | None

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end__gt=models.F("start")),
                name="report_embedding_nonempty_span",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        examination_annotation__isnull=False,
                        finding_annotation__isnull=True,
                        classification_annotation__isnull=True,
                    )
                    | models.Q(
                        examination_annotation__isnull=True,
                        finding_annotation__isnull=False,
                        classification_annotation__isnull=True,
                    )
                    | models.Q(
                        examination_annotation__isnull=True,
                        finding_annotation__isnull=True,
                        classification_annotation__isnull=False,
                    )
                ),
                name="report_embedding_one_annotation",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        try:
            TextEmbedding.model_validate(self.embedding)
        except ValueError as exc:
            raise ValidationError({"embedding": str(exc)}) from exc
        # Source ownership and clinical target checks are shared with capture/export.
        from endoreg_db.services.reports.concept_embeddings import (
            validate_record_source,
        )

        validate_record_source(self)

    def save(self, *args: object, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        if not self._state.adding:
            raise ValidationError(
                "Report concept embeddings are immutable; capture a new revision"
            )
        self.full_clean()
        super().save(*args, **kwargs)
