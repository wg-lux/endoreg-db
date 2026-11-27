"""Compatibility import path for the lx_dtypes-owned report-draft contract."""

from lx_dtypes.models.contracts.report_draft import (
    REPORT_DRAFT_SCHEMA_VERSION,
    PatientExaminationReportDraft,
    ReportDraftTemplateIdentity,
    dump_patient_examination_report_draft,
)


__all__ = [
    "PatientExaminationReportDraft",
    "REPORT_DRAFT_SCHEMA_VERSION",
    "ReportDraftTemplateIdentity",
    "dump_patient_examination_report_draft",
]
