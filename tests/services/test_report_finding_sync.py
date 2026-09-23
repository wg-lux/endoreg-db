from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

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
from endoreg_db.models.medical.examination.examination import Examination
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


def test_repeated_polyp_instances_keep_children_and_identity_on_replay() -> None:
    examination, finding, classification, choice, intervention = _create_graph()
    second_intervention = FindingIntervention.objects.create(name="second_resection")
    finding.finding_interventions.add(second_intervention)
    first_id, second_id = uuid4(), uuid4()
    payload: list[dict[str, object]] = [
        {
            "finding_id": finding.pk,
            "instance_id": str(first_id),
            "classifications": [
                {
                    "classification_id": classification.pk,
                    "classification_choice_id": choice.pk,
                }
            ],
            "interventions": [{"intervention_id": intervention.pk}],
        },
        {
            "finding_id": finding.pk,
            "instance_id": str(second_id),
            "interventions": [{"intervention_id": second_intervention.pk}],
        },
    ]
    sync_report_findings(examination, payload, user=None)
    first = PatientFinding.objects.get(instance_id=first_id)
    second = PatientFinding.objects.get(instance_id=second_id)
    first_pk, second_pk = first.pk, second.pk
    sync_report_findings(examination, list(reversed(payload)), user=None)
    assert PatientFinding.objects.count() == 2
    assert PatientFinding.objects.get(instance_id=first_id).pk == first_pk
    assert PatientFinding.objects.get(instance_id=second_id).pk == second_pk
    assert (
        PatientFindingClassification.objects.get(is_active=True).finding.pk == first_pk
    )
    assert (
        PatientFindingIntervention.objects.get(intervention=intervention).finding.pk
        == first_pk
    )
    assert (
        PatientFindingIntervention.objects.get(
            intervention=second_intervention
        ).finding.pk
        == second_pk
    )


@pytest.mark.parametrize(
    "identity",
    [
        "missing",
        "duplicate_uuid",
        "duplicate_pk",
        "foreign",
        "inactive",
        "wrong_type",
        "disagree",
    ],
)
def test_ambiguous_or_conflicting_instance_identity_rolls_back(identity: str) -> None:
    examination, finding, _classification, _choice, intervention = _create_graph()
    first = PatientFinding.objects.create(
        patient_examination=examination, finding=finding
    )
    second = PatientFinding.objects.create(
        patient_examination=examination, finding=finding
    )
    payload: list[dict[str, object]] = [{"finding_id": finding.pk}]
    if identity == "duplicate_uuid":
        entry: dict[str, object] = {
            "finding_id": finding.pk,
            "instance_id": str(uuid4()),
        }
        payload = [entry, entry]
    elif identity == "duplicate_pk":
        entry = {"finding_id": finding.pk, "patient_finding_id": first.pk}
        payload = [entry, entry]
    elif identity in {"foreign", "inactive", "wrong_type", "disagree"}:
        if identity == "foreign":
            other = PatientExamination.objects.create(patient=examination.patient)
            second.patient_examination = other
            second.save(update_fields=["patient_examination"])
        elif identity == "inactive":
            second.is_active = False
            second.save(update_fields=["is_active"])
        elif identity == "wrong_type":
            second.finding = Finding.objects.create(name="another_finding")
            second.save(update_fields=["finding"])
        payload = [
            {
                "finding_id": finding.pk,
                "patient_finding_id": first.pk,
                "interventions": [{"intervention_id": intervention.pk}],
            },
            {"finding_id": finding.pk, "patient_finding_id": second.pk},
        ]
        if identity == "disagree":
            payload[1]["instance_id"] = str(first.instance_id)
    with pytest.raises(ValidationError):
        sync_report_findings(examination, payload, user=None)
    assert PatientFinding.objects.count() == 2
    assert PatientFindingIntervention.objects.count() == 0


def test_legacy_singleton_submission_reuses_original_primary_key() -> None:
    examination, finding, _classification, _choice, _intervention = _create_graph()
    original = PatientFinding.objects.create(
        patient_examination=examination, finding=finding
    )
    sync_report_findings(examination, [{"finding_id": finding.pk}], user=None)
    assert PatientFinding.objects.get().pk == original.pk


def test_unkeyed_repeated_creation_is_rejected_before_writes() -> None:
    examination, finding, _classification, _choice, _intervention = _create_graph()
    with pytest.raises(ValidationError, match="explicit instance identities"):
        sync_report_findings(
            examination,
            [{"finding_id": finding.pk}, {"finding_id": finding.pk}],
            user=None,
        )
    assert not PatientFinding.objects.exists()


def test_explicit_instance_update_does_not_modify_other_polyp() -> None:
    examination, finding, _classification, _choice, intervention = _create_graph()
    first = PatientFinding.objects.create(
        patient_examination=examination, finding=finding
    )
    second = PatientFinding.objects.create(
        patient_examination=examination, finding=finding
    )
    sync_report_findings(
        examination,
        [
            {
                "finding_id": finding.pk,
                "patient_finding_id": first.pk,
                "interventions": [{"intervention_id": intervention.pk}],
            },
            {"finding_id": finding.pk, "patient_finding_id": second.pk},
        ],
        user=None,
    )
    assert PatientFindingIntervention.objects.get().finding.pk == first.pk
    assert not second.interventions.exists()


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
    finding.finding_classifications.add(classification)
    finding.finding_interventions.add(intervention)
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


def test_sync_report_findings_rejects_classification_not_linked_to_finding() -> None:
    patient_examination, finding, _classification, choice, _intervention = (
        _create_graph()
    )
    unrelated = FindingClassification.objects.create(name="unrelated-classification")
    unrelated.choices.add(choice)

    with pytest.raises(ValidationError, match="not allowed for this finding"):
        sync_report_findings(
            patient_examination,
            [
                {
                    "finding": finding.pk,
                    "classifications": [
                        {
                            "classification": unrelated.pk,
                            "classification_choice": choice.pk,
                        }
                    ],
                }
            ],
            user=None,
        )

    assert PatientFinding.objects.count() == 0


def test_sync_report_findings_rejects_choice_not_linked_to_classification() -> None:
    patient_examination, finding, classification, _choice, _intervention = (
        _create_graph()
    )
    unrelated_choice = FindingClassificationChoice.objects.create(
        name="unrelated-choice",
        subcategories={},
        numerical_descriptors={},
    )

    with pytest.raises(ValidationError, match="not allowed for this classification"):
        sync_report_findings(
            patient_examination,
            [
                {
                    "finding": finding.pk,
                    "classifications": [
                        {
                            "classification": classification.pk,
                            "classification_choice": unrelated_choice.pk,
                        }
                    ],
                }
            ],
            user=None,
        )

    assert PatientFinding.objects.count() == 0


def test_sync_report_findings_rejects_intervention_not_linked_to_finding() -> None:
    patient_examination, finding, _classification, _choice, _intervention = (
        _create_graph()
    )
    unrelated = FindingIntervention.objects.create(name="unrelated-intervention")

    with pytest.raises(ValidationError, match="not allowed for this finding"):
        sync_report_findings(
            patient_examination,
            [
                {
                    "finding": finding.pk,
                    "interventions": [{"intervention": unrelated.pk}],
                }
            ],
            user=None,
        )

    assert PatientFinding.objects.count() == 0


def test_sync_report_findings_rejects_finding_not_linked_to_examination() -> None:
    patient_examination, finding, _classification, _choice, _intervention = (
        _create_graph()
    )
    examination = Examination.objects.create(name="report-sync-examination")
    patient_examination.examination = examination
    patient_examination.save(update_fields=["examination"])

    with pytest.raises(ValidationError, match="not allowed for this examination"):
        sync_report_findings(
            patient_examination,
            [{"finding": finding.pk}],
            user=None,
        )

    assert PatientFinding.objects.count() == 0


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
