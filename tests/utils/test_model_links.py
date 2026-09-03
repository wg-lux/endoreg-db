from __future__ import annotations

from typing import cast

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.utils.links import ModelLinks
from types import SimpleNamespace


def _patient(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _lab_value(patient: SimpleNamespace | None = None) -> SimpleNamespace:
    sample = SimpleNamespace(sample=SimpleNamespace(patient=patient))
    return SimpleNamespace(patient=patient, sample=sample)


def _patient_examination(patient: SimpleNamespace | None = None) -> SimpleNamespace:
    return SimpleNamespace(patient=patient)


def test_get_first_patient_prefers_lab_values_before_examinations() -> None:
    direct_patient = _patient("direct")
    fallback_patient = _patient("fallback")
    links = ModelLinks(
        patient_lab_values=cast(list[PatientLabValue], [_lab_value(direct_patient)]),
        patient_examinations=cast(
            list[PatientExamination], [_patient_examination(fallback_patient)]
        ),
    )

    assert links.get_first_patient() == direct_patient


def test_get_first_patient_checks_all_patient_sections_in_order() -> None:
    fallback = _patient("exam")
    links = ModelLinks(
        patient_lab_values=[],
        patient_examinations=cast(
            list[PatientExamination], [_patient_examination(fallback)]
        ),
    )

    assert links.get_first_patient() == fallback


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
