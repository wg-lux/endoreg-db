from __future__ import annotations

# pyright: reportUnknownMemberType=false

from datetime import date

import pytest

from endoreg_db.models import (
    AnonymExaminationReport,
    Center,
    Patient,
    PatientExamination,
    RawPdfFile,
    SensitiveMeta,
)
from endoreg_db.services.report_materialization import (
    build_report_context_from_validation,
    upsert_anonym_examination_report_from_pdf,
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


@pytest.mark.django_db
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="no-payload"),
        pytest.param({}, id="missing-anonymized-text"),
        pytest.param(
            {"anonymized_text": "   "},
            id="blank-anonymized-text",
        ),
    ],
)
def test_materialization_never_falls_back_to_raw_report_text(
    payload: dict[str, str] | None,
) -> None:
    raw_text_sentinel = "RAW-PHI-SENTINEL-MUST-NOT-BE-MATERIALIZED"
    center = Center.objects.create(name="fail-closed-materialization-center")
    patient = Patient.objects.create(
        patient_hash="fail-closed-patient-hash",
        center=center,
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    sensitive_meta = SensitiveMeta.objects.create(
        center=center,
        examination_date=date(2024, 2, 15),
        patient_hash="fail-closed-patient-hash",
        examination_hash="fail-closed-exam-hash",
        pseudo_patient=patient,
        pseudo_examination=patient_examination,
        anonymized_text="",
    )
    pdf = RawPdfFile.objects.create(
        center=center,
        examination=patient_examination,
        sensitive_meta=sensitive_meta,
        text=raw_text_sentinel,
        anonymized_text="",
    )

    with pytest.raises(ValueError, match="without non-empty anonymized text"):
        upsert_anonym_examination_report_from_pdf(
            pdf=pdf,
            payload=payload,
            document_type_name="report_final",
        )

    pdf.refresh_from_db()
    assert pdf.anonym_examination_report_id is None
    assert not AnonymExaminationReport.objects.filter(
        text__contains=raw_text_sentinel
    ).exists()
