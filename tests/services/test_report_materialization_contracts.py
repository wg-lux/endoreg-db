from __future__ import annotations

from datetime import date

import pytest

from endoreg_db.models import (
    Center,
    Patient,
    PatientExamination,
    RawPdfFile,
    SensitiveMeta,
)
from endoreg_db.services.report_materialization import (
    build_report_context_from_validation,
)


@pytest.mark.django_db
def test_build_report_context_from_validation_uses_contract_fields() -> None:
    center = Center.objects.create(name="contract-center")
    patient = Patient.objects.create(
        patient_hash="contract-patient-hash",
        center=center,
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    sensitive_meta = SensitiveMeta.objects.create(
        center=center,
        examination_date=date(2024, 2, 15),
        patient_hash="patient-hash",
        examination_hash="exam-hash",
        pseudo_patient=patient,
        pseudo_examination=patient_examination,
    )

    pdf = RawPdfFile.objects.create(
        center=center,
        sensitive_meta=sensitive_meta,
        text="raw report text",
    )

    report_context = build_report_context_from_validation(
        pdf=pdf,
        payload={"anonymized_text": "validated report text"},
        document_type_name="report_final",
    )

    sensitive_meta.refresh_from_db()
    assert report_context.patient_id == sensitive_meta.pseudo_patient_id
    assert report_context.patient_examination_id == sensitive_meta.pseudo_examination_id
    assert report_context.document_type.value == "report_final"
    assert report_context.anonymized_text == "validated report text"
    assert report_context.patient_hash == sensitive_meta.patient_hash
    assert report_context.examination_hash == sensitive_meta.examination_hash
    assert report_context.source_pdf_id == pdf.pk
