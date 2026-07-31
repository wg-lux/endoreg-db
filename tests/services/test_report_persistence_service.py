from __future__ import annotations

# pyright: reportUnknownMemberType=false

import pytest
from django.utils import timezone

from endoreg_db.models import Patient, PatientExamination, PatientExaminationReport
from endoreg_db.services.report_persistence import save_report_submission


@pytest.mark.django_db
def test_save_report_submission_final_purges_patient_examination_draft() -> None:
    patient = Patient.objects.create(
        patient_hash="report-purge-patient",
        first_name="Report",
        last_name="Purge",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination.report_draft = {
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
        "payload": {"sections": [{"id": "findings"}]},
    }
    patient_examination.draft_updated_at = timezone.now()
    patient_examination.save(update_fields=["report_draft", "draft_updated_at"])

    result = save_report_submission(
        patient_examination_id=patient_examination.pk,
        template_name="star_upper_gi_main",
        editor_payload={"sections": [{"id": "findings"}]},
        rendered_text="Final report text",
        status=PatientExaminationReport.Status.FINAL,
    )

    assert result.report.status == PatientExaminationReport.Status.FINAL

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}
    assert patient_examination.draft_updated_at is None
