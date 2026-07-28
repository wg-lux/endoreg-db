from __future__ import annotations

from datetime import date, datetime

import pytest
from django.contrib.auth.models import User
from rest_framework.exceptions import ValidationError

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
    FindingClassificationChoice,
)
from endoreg_db.models.medical.finding.finding_intervention import FindingIntervention
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.medical.patient.patient_finding_intervention import (
    PatientFindingIntervention,
)
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.report_finding_sync import sync_report_findings
from endoreg_db.services.report_persistence import save_report_submission

pytestmark = pytest.mark.django_db


def _create_graph() -> tuple[
    PatientExamination,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,
]:
    patient = Patient.objects.create(
        patient_hash="report-finding-sync-patient",
        first_name="Report",
        last_name="Finding Sync",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    finding = Finding.objects.create(name="report_sync_finding")
    classification = FindingClassification.objects.create(
        name="report_sync_classification"
    )
    choice = FindingClassificationChoice.objects.create(
        name="report_sync_choice",
        description="Report sync choice",
        subcategories={},
        numerical_descriptors={},
    )
    classification.choices.add(choice)
    intervention = FindingIntervention.objects.create(name="report_sync_intervention")
    return patient_examination, finding, classification, choice, intervention


def test_sync_report_findings_updates_and_clears_intervention_temporal_fields() -> None:
    patient_examination, finding, _classification, _choice, intervention = (
        _create_graph()
    )

    sync_report_findings(
        patient_examination,
        [
            {
                "finding": finding.name,
                "interventions": [
                    {
                        "intervention": intervention.name,
                        "state": "done",
                        "date": "2025-03-04",
                        "time_start": "2025-03-04T09:30:00Z",
                        "time_end": "2025-03-04T09:45:00Z",
                    }
                ],
            }
        ],
        user=None,
    )

    persisted = PatientFindingIntervention.objects.get()
    persisted_id = persisted.pk
    assert persisted.date == date(2025, 3, 4)
    assert persisted.time_start == datetime.fromisoformat("2025-03-04T09:30:00+00:00")
    assert persisted.time_end == datetime.fromisoformat("2025-03-04T09:45:00+00:00")

    sync_report_findings(
        patient_examination,
        [
            {
                "finding": finding.pk,
                "interventions": [
                    {
                        "intervention": intervention.pk,
                        "state": "done",
                    }
                ],
            }
        ],
        user=None,
    )

    persisted.refresh_from_db()
    assert persisted.pk == persisted_id
    assert persisted.date is None
    assert persisted.time_start is None
    assert persisted.time_end is None
    assert PatientFindingIntervention.objects.count() == 1


def test_sync_report_findings_deactivates_then_replaces_inactive_intervention() -> None:
    patient_examination, finding, _classification, _choice, intervention = (
        _create_graph()
    )
    payload = [
        {
            "finding": finding.name,
            "interventions": [
                {
                    "intervention": intervention.name,
                    "state": "done",
                }
            ],
        }
    ]
    sync_report_findings(patient_examination, payload, user=None)
    original = PatientFindingIntervention.objects.get()

    sync_report_findings(
        patient_examination,
        [{"finding": finding.name, "interventions": []}],
        user=None,
    )
    original.refresh_from_db()
    assert original.is_active is False

    sync_report_findings(patient_examination, payload, user=None)

    original.refresh_from_db()
    replacement = PatientFindingIntervention.objects.exclude(pk=original.pk).get()
    assert original.is_active is False
    assert replacement.is_active is True


def test_sync_report_findings_deactivates_then_replaces_inactive_classification() -> (
    None
):
    patient_examination, finding, classification, choice, _intervention = (
        _create_graph()
    )
    payload: list[dict[str, object]] = [
        {
            "finding": finding.name,
            "classifications": [
                {
                    "classification": classification.name,
                    "classification_choice": choice.name,
                }
            ],
        }
    ]
    sync_report_findings(patient_examination, payload, user=None)
    original = PatientFindingClassification.objects.get()

    sync_report_findings(
        patient_examination,
        [{"finding": finding.name, "classifications": []}],
        user=None,
    )
    original.refresh_from_db()
    assert original.is_active is False

    sync_report_findings(patient_examination, payload, user=None)

    original.refresh_from_db()
    replacement = PatientFindingClassification.objects.exclude(pk=original.pk).get()
    assert original.is_active is False
    assert replacement.is_active is True


def test_sync_report_findings_preserves_duplicate_new_intervention_items() -> None:
    patient_examination, finding, _classification, _choice, intervention = (
        _create_graph()
    )
    duplicate_item = {
        "intervention": intervention.name,
        "state": "planned",
    }

    sync_report_findings(
        patient_examination,
        [
            {
                "finding": finding.name,
                "interventions": [duplicate_item, duplicate_item],
            }
        ],
        user=None,
    )

    assert (
        PatientFindingIntervention.objects.filter(
            intervention=intervention,
            state="planned",
            is_active=True,
        ).count()
        == 2
    )


def test_sync_report_findings_reconciles_classifications_and_deactivates_findings() -> (
    None
):
    patient_examination, finding, classification, choice, _intervention = (
        _create_graph()
    )
    user = User.objects.create_user(username="report-finding-sync-user")
    payload: list[dict[str, object]] = [
        {
            "finding": finding.name,
            "classifications": [
                {
                    "classification": classification.name,
                    "classification_choice": choice.name,
                    "subcategories": {},
                    "numerical_descriptors": {},
                }
            ],
        }
    ]
    sync_report_findings(
        patient_examination,
        payload,
        user=user,
    )

    patient_finding = PatientFinding.objects.get()
    classification_row = PatientFindingClassification.objects.get()
    assert patient_finding.is_active is True
    assert classification_row.is_active is True

    sync_report_findings(patient_examination, [], user=user)

    patient_finding.refresh_from_db()
    assert patient_finding.is_active is False
    assert patient_finding.deactivated_at is not None


def test_save_report_submission_rolls_back_unknown_nested_intervention() -> None:
    patient_examination, finding, _classification, _choice, _intervention = (
        _create_graph()
    )

    with pytest.raises(ValidationError, match="Unknown intervention"):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="report_sync_template",
            findings=[
                {
                    "finding": finding.name,
                    "interventions": [{"intervention": "missing-intervention"}],
                }
            ],
        )

    assert PatientFinding.objects.count() == 0
    assert PatientExaminationReport.objects.count() == 0


def test_sync_report_findings_preserves_date_parsing_exception_types() -> None:
    patient_examination, finding, _classification, _choice, intervention = (
        _create_graph()
    )

    with pytest.raises(ValueError):
        sync_report_findings(
            patient_examination,
            [
                {
                    "finding": finding.name,
                    "interventions": [
                        {
                            "intervention": intervention.name,
                            "date": "not-an-iso-date",
                        }
                    ],
                }
            ],
            user=None,
        )

    with pytest.raises(ValidationError):
        sync_report_findings(
            patient_examination,
            [
                {
                    "finding": finding.name,
                    "interventions": [
                        {
                            "intervention": intervention.name,
                            "date": 123,
                        }
                    ],
                }
            ],
            user=None,
        )
