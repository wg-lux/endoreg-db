"""Immutable cohort-scoped matches of study text rules against one anonymized report."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Unpack
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.study_text_extraction import (
    StudyTextExtractionEvidence,
)
from pydantic import ValidationError as SchemaError

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.medical.patient.patient_examination import PatientExamination
    from endoreg_db.models.other.study_cohort import StudyCohort


class StudyReportTextExtraction(models.Model):
    """Evidence only: approval creates findings explicitly and separately."""

    if TYPE_CHECKING:
        report_id: int
        patient_examination_id: int

    objects = models.Manager["StudyReportTextExtraction"]()
    uuid: models.UUIDField[UUID, UUID] = models.UUIDField(
        default=uuid4, unique=True, editable=False
    )
    cohort: models.ForeignKey[StudyCohort] = models.ForeignKey(
        "StudyCohort", on_delete=models.CASCADE, related_name="report_text_extractions"
    )
    cohort_definition_sha256: models.CharField[str, str] = models.CharField(
        max_length=64
    )
    report: models.ForeignKey[RawPdfFile] = models.ForeignKey(
        "RawPdfFile", on_delete=models.CASCADE, related_name="study_text_extractions"
    )
    patient_examination: models.ForeignKey[PatientExamination] = models.ForeignKey(
        "PatientExamination",
        on_delete=models.CASCADE,
        related_name="study_report_text_extractions",
    )
    evidence: models.JSONField[JsonObject, JsonObject] = models.JSONField()
    created_by: models.ForeignKey[User] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="study_report_text_extractions",
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "cohort_definition_sha256", "report"],
                name="unique_study_report_text_extraction",
            )
        ]

    def validated_evidence(self) -> StudyTextExtractionEvidence:
        try:
            evidence = StudyTextExtractionEvidence.model_validate_json(
                json.dumps(self.evidence)
            )
        except SchemaError as error:
            raise ValidationError("Invalid study report text evidence") from error
        if evidence.cohort_definition_sha256 != self.cohort_definition_sha256:
            raise ValidationError("Evidence belongs to another cohort definition")
        examination = self.patient_examination
        if (
            examination.knowledge_base_module,
            examination.knowledge_base_version,
        ) != (evidence.knowledge_base_module, evidence.knowledge_base_version):
            raise ValidationError("Evidence terminology differs from the examination")
        from endoreg_db.services.reports.text_finding_approval import (
            report_examination,
        )

        report = self.report
        linked = report_examination(report)
        if linked is None or linked.pk != examination.pk:
            raise ValidationError("Report is not linked to the examination")
        text = report.anonymized_text or ""
        if any(
            mention.end > len(text)
            for mention in (*evidence.mentions, *evidence.classification_mentions)
        ):
            raise ValidationError("Evidence no longer matches the report text")
        return evidence

    def clean(self) -> None:
        super().clean()
        if self.pk is not None:
            raise ValidationError("Study report text evidence is immutable")
        self.evidence = self.validated_evidence().model_dump(mode="json")

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
        self.full_clean()
        super().save(**kwargs)
