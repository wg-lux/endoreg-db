from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from django.db import models

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_disease import PatientDisease
from endoreg_db.models.medical.patient.patient_event import PatientEvent
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_lab_sample import PatientLabSample
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.models.medical.patient.patient_medication import PatientMedication
from endoreg_db.models.medical.patient.patient_medication_schedule import (
    PatientMedicationSchedule,
)
from endoreg_db.utils.links import ModelLinks
from types import SimpleNamespace

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("has_sample_patient", [False, True])
def test_get_first_patient_prefers_lab_values_before_examinations(
    has_sample_patient: bool,
) -> None:
    direct_patient, sample_patient, fallback_patient = Patient(), Patient(), Patient()
    links = ModelLinks(
        patient_lab_values=[
            PatientLabValue(
                patient=direct_patient,
                sample=PatientLabSample(patient=sample_patient)
                if has_sample_patient
                else None,
            )
        ],
        patient_examinations=[PatientExamination(patient=fallback_patient)],
    )

    result = links.get_first_patient()

    assert result is (sample_patient if has_sample_patient else direct_patient)


@pytest.mark.parametrize("first_section", range(7))
def test_get_first_patient_checks_all_patient_sections_in_order(
    first_section: int,
) -> None:
    sections: tuple[tuple[str, Callable[[Patient | None], models.Model]], ...] = (
        ("patient_lab_samples", lambda patient: PatientLabSample(patient=patient)),
        ("patient_examinations", lambda patient: PatientExamination(patient=patient)),
        ("patient_diseases", lambda patient: PatientDisease(patient=patient)),
        ("patient_events", lambda patient: PatientEvent(patient=patient)),
        (
            "patient_findings",
            lambda patient: (
                PatientFinding(patient_examination=PatientExamination(patient=patient))
                if patient is not None
                else PatientFinding()
            ),
        ),
        ("patient_medications", lambda patient: PatientMedication(patient=patient)),
        (
            "patient_medication_schedules",
            lambda patient: PatientMedicationSchedule(patient=patient),
        ),
    )
    expected, later = Patient(), Patient()
    records = {
        name: [
            make_record(None),
            make_record(expected if offset == 0 else later),
            make_record(later),
        ]
        for offset, (name, make_record) in enumerate(sections[first_section:])
    }
    links = ModelLinks.model_validate(records)
    before = {name: tuple(values) for name, values in links.active().items()}

    result = links.get_first_patient()

    assert result is expected
    assert {name: tuple(values) for name, values in links.active().items()} == before


def test_get_first_patient_without_patient_returns_none() -> None:
    links = ModelLinks(patient_lab_values=[PatientLabValue()])

    assert links.get_first_patient() is None
    assert ModelLinks().get_first_patient() is None


def test_match_any_detects_overlap_and_no_overlap() -> None:
    shared_exam = SimpleNamespace(pk=123)
    lhs = ModelLinks(
        examinations=cast(list[Examination], [shared_exam, SimpleNamespace(pk=456)]),
        findings=cast(list[Finding], []),
    )
    rhs = ModelLinks(
        examinations=cast(list[Examination], [shared_exam]),
        findings=cast(list[Finding], [SimpleNamespace(pk=456)]),
    )
    no_overlap = ModelLinks(
        examinations=cast(list[Examination], [SimpleNamespace(pk=999)])
    )

    assert lhs.match_any(rhs) is True
    assert lhs.match_any(no_overlap) is False


def test_active_includes_only_non_empty_lists() -> None:
    links = ModelLinks(
        examinations=cast(list[Examination], [SimpleNamespace(pk=1)]),
        findings=cast(list[Finding], [SimpleNamespace(pk=2)]),
        lab_values=[],
        patient_examinations=[],
    )

    active = links.active()

    assert set(active.keys()) == {"examinations", "findings"}
    assert len(active["examinations"]) == 1
    assert len(active["findings"]) == 1


def test_repr_reports_field_counts() -> None:
    links = ModelLinks(
        examinations=cast(
            list[Examination], [SimpleNamespace(pk=1), SimpleNamespace(pk=2)]
        ),
        findings=cast(list[Finding], [SimpleNamespace(pk=3)]),
    )

    assert "ModelLinks(" in repr(links)
    assert "examinations=2" in repr(links)
    assert "findings=1" in repr(links)
