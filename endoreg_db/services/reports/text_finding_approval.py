"""Persist an approved report-text suggestion with server-derived provenance."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from django.contrib.auth.models import User as AuthUser
from django.db import transaction
from rest_framework.exceptions import ValidationError

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.services.reports.finding_sync import sync_report_findings
from endoreg_db.services.reports.structured_records import (
    persist_patient_examination_dtypes_record_from_ledger,
)


def report_examination(report: RawPdfFile) -> PatientExamination | None:
    """Return the examination a report belongs to, matching study cohort linkage."""
    if report.examination is not None:
        return report.examination
    full_report = report.anonym_examination_report
    if full_report is not None and full_report.patient_examination is not None:
        return full_report.patient_examination
    if report.sensitive_meta is not None:
        return report.sensitive_meta.pseudo_examination
    return None


@transaction.atomic
def approve_text_finding(
    *,
    report: RawPdfFile,
    patient_examination: PatientExamination,
    finding_name: str,
    instance_id: UUID,
    classifications: Sequence[tuple[str, str]],
    user: AuthUser,
) -> PatientFinding:
    """Create or re-confirm one finding taken from the report's anonymized text.

    Callers verify the suggestion; this records provenance, validates
    classifications against the finding and refreshes the structured ledger.
    """
    linked = report_examination(report)
    if linked is None or linked.pk != patient_examination.pk:
        raise ValidationError(
            {"findings": "Report is not linked to this patient examination."}
        )
    existing = PatientFinding.objects.filter(instance_id=instance_id).first()
    if existing is not None and (
        existing.source != str(PatientFinding.Source.REPORT_TEXT)
        or existing.source_report_id != report.pk
    ):
        raise ValidationError(
            {"findings": "Finding instance already exists with another provenance."}
        )
    sync_report_findings(
        patient_examination,
        [
            {
                "finding": finding_name,
                "instance_id": str(instance_id),
                "classifications": [
                    {"classification": classification, "classification_choice": choice}
                    for classification, choice in classifications
                ],
            }
        ],
        user=user,
        replace=False,
    )
    patient_finding = PatientFinding.objects.get(instance_id=instance_id)
    if (
        patient_finding.source != str(PatientFinding.Source.REPORT_TEXT)
        or patient_finding.source_report_id != report.pk
    ):
        patient_finding.source = str(PatientFinding.Source.REPORT_TEXT)
        patient_finding.source_report = report
        patient_finding.save(update_fields=["source", "source_report"])
    persist_patient_examination_dtypes_record_from_ledger(patient_examination)
    return patient_finding


__all__ = ["approve_text_finding", "report_examination"]
