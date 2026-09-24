from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from typing import Callable, cast

import pytest
from django.test import TestCase

from endoreg_db.models import (
    Center,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
    FindingIntervention,
    Patient,
    PatientExamination,
    PatientFinding,
    PatientFindingClassification,
    PatientFindingIntervention,
)

from endoreg_db.services import report_history
from lx_dtypes.models.contracts.patient_examination_report import (
    PatientFindingClassificationHistoryData,
    PatientFindingInterventionHistoryData,
)

_serialize_patient_finding_classification = cast(
    Callable[[object], PatientFindingClassificationHistoryData],
    getattr(report_history, "_serialize_patient_finding_classification"),
)
_serialize_patient_finding_intervention = cast(
    Callable[[object], PatientFindingInterventionHistoryData],
    getattr(report_history, "_serialize_patient_finding_intervention"),
)


def test_report_history_serializes_temporal_and_json_values() -> None:
    intervention = SimpleNamespace(
        pk=7,
        intervention_id=11,
        intervention=SimpleNamespace(name="Polypectomy"),
        state="completed",
        date=date(2026, 8, 20),
        time_start=time(10, 15, 30),
        time_end=None,
    )
    classification = SimpleNamespace(
        pk=8,
        classification_id=12,
        classification_choice_id=13,
        classification=SimpleNamespace(name="Paris"),
        classification_choice=SimpleNamespace(name="0-Is"),
        subcategories={"reviewed_at": date(2026, 8, 20)},
        numerical_descriptors={"size_mm": 8},
    )

    assert _serialize_patient_finding_intervention(intervention) == {
        "id": 7,
        "intervention_id": 11,
        "intervention_name": "Polypectomy",
        "state": "completed",
        "date": "2026-08-20",
        "time_start": "10:15:30",
        "time_end": None,
    }
    assert _serialize_patient_finding_classification(classification) == {
        "id": 8,
        "classification_id": 12,
        "classification_choice_id": 13,
        "classification_name": "Paris",
        "classification_choice_name": "0-Is",
        "subcategories": {"reviewed_at": "2026-08-20"},
        "numerical_descriptors": {"size_mm": 8},
    }


def test_report_history_rejects_non_object_json_fields() -> None:
    classification = SimpleNamespace(
        pk=8,
        classification_id=None,
        classification_choice_id=None,
        classification=None,
        classification_choice=None,
        subcategories=["not", "an", "object"],
        numerical_descriptors={},
    )

    with pytest.raises(ValueError, match="must contain an object"):
        _serialize_patient_finding_classification(classification)


class ReportHistoryContextTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(name="history_context_center")
        self.patient = Patient.objects.create(
            patient_hash="history_context_patient",
            center=self.center,
            is_real_person=False,
        )
        self.current = PatientExamination.objects.create(
            patient=self.patient, date_start=date(2026, 8, 20)
        )

    def test_only_strictly_earlier_examinations_are_selected_before_limit(self) -> None:
        older = PatientExamination.objects.create(
            patient=self.patient, date_start=date(2026, 1, 1)
        )
        latest_prior = PatientExamination.objects.create(
            patient=self.patient, date_start=date(2026, 8, 19)
        )
        for examination_date in (None, date(2026, 8, 20), date(2027, 1, 1)):
            PatientExamination.objects.create(
                patient=self.patient, date_start=examination_date
            )
        context = report_history.get_patient_examination_history_context(
            self.current, limit=1
        )
        assert [
            row["patient_examination_id"] for row in context["previous_examinations"]
        ] == [latest_prior.pk]
        context = report_history.get_patient_examination_history_context(self.current)
        assert [
            row["patient_examination_id"] for row in context["previous_examinations"]
        ] == [latest_prior.pk, older.pk]

    def test_hash_matching_is_limited_to_pseudonyms_in_the_same_center(self) -> None:
        other_center = Center.objects.create(name="history_other_center")
        duplicate = Patient.objects.create(
            patient_hash=self.patient.patient_hash,
            center=self.center,
            is_real_person=False,
        )
        expected = PatientExamination.objects.create(
            patient=duplicate, date_start=date(2026, 7, 1)
        )
        for center, patient_hash, is_real_person in (
            (other_center, self.patient.patient_hash, False),
            (None, self.patient.patient_hash, False),
            (self.center, "another_identity", False),
            (self.center, self.patient.patient_hash, True),
        ):
            foreign_patient = Patient.objects.create(
                center=center, patient_hash=patient_hash, is_real_person=is_real_person
            )
            PatientExamination.objects.create(
                patient=foreign_patient, date_start=date(2026, 8, 19)
            )
        context = report_history.get_patient_examination_history_context(
            self.current, limit=1
        )
        assert [
            row["patient_examination_id"] for row in context["previous_examinations"]
        ] == [expected.pk]

    def test_missing_current_date_has_no_prior_context(self) -> None:
        PatientExamination.objects.create(
            patient=self.patient, date_start=date(2026, 1, 1)
        )
        self.current.date_start = None
        with self.assertNumQueries(0):
            context = report_history.get_patient_examination_history_context(
                self.current
            )
        assert context["previous_examinations"] == []

    def test_blank_hash_keeps_only_the_explicit_patient_relation(self) -> None:
        for patient_hash in (None, "", "   "):
            with self.subTest(patient_hash=patient_hash):
                self.patient.patient_hash = patient_hash
                self.patient.save(update_fields=["patient_hash"])
                duplicate = Patient.objects.create(
                    patient_hash=patient_hash, center=self.center, is_real_person=False
                )
                PatientExamination.objects.create(
                    patient=duplicate, date_start=date(2026, 1, 1)
                )
                context = report_history.get_patient_examination_history_context(
                    self.current
                )
                assert context["previous_examinations"] == []

    def test_real_patient_or_unknown_center_does_not_expand_identity(self) -> None:
        own_prior = PatientExamination.objects.create(
            patient=self.patient, date_start=date(2026, 1, 1)
        )
        for is_real_person, center in ((True, self.center), (False, None)):
            with self.subTest(
                is_real_person=is_real_person,
                center_id=center.pk if center is not None else None,
            ):
                self.patient.is_real_person = is_real_person
                self.patient.center = center
                self.patient.save(update_fields=["is_real_person", "center"])
                duplicate = Patient.objects.create(
                    patient_hash=self.patient.patient_hash,
                    center=center,
                    is_real_person=is_real_person,
                )
                PatientExamination.objects.create(
                    patient=duplicate, date_start=date(2026, 8, 19)
                )
                context = report_history.get_patient_examination_history_context(
                    self.current
                )
                assert [
                    row["patient_examination_id"]
                    for row in context["previous_examinations"]
                ] == [own_prior.pk]

    def test_active_finding_context_uses_three_queries_for_multiple_examinations(
        self,
    ) -> None:
        earlier = PatientExamination.objects.create(
            patient=self.patient, date_start=date(2026, 1, 1)
        )
        classification = FindingClassification.objects.create(name="history_morphology")
        choice = FindingClassificationChoice.objects.create(
            name="history_morphology_choice", subcategories={}, numerical_descriptors={}
        )
        classification.choices.add(choice)
        intervention = FindingIntervention.objects.create(name="history_resection")
        for examination in (earlier, self.current):
            for index in range(3):
                finding_type, _ = Finding.objects.get_or_create(
                    name=f"history_finding_{index}"
                )
                finding = PatientFinding.objects.create(
                    patient_examination=examination, finding=finding_type
                )
                for active in (True, False):
                    PatientFindingClassification.objects.create(
                        finding=finding,
                        classification=classification,
                        classification_choice=choice,
                        is_active=active,
                    )
                    PatientFindingIntervention.objects.create(
                        finding=finding, intervention=intervention, is_active=active
                    )
                PatientFinding.objects.create(
                    patient_examination=examination,
                    finding=finding_type,
                    is_active=False,
                )
        with self.assertNumQueries(3):
            summaries = report_history.get_patient_finding_summaries(
                [earlier.pk, self.current.pk]
            )
        assert set(summaries) == {earlier.pk, self.current.pk}
        for findings in summaries.values():
            assert len(findings) == 3
            for finding in findings:
                assert len(finding["classifications"]) == 1
                assert len(finding["interventions"]) == 1
                assert (
                    finding["classifications"][0]["classification_name"]
                    == classification.name
                )
                assert (
                    finding["interventions"][0]["intervention_name"]
                    == intervention.name
                )
        with self.assertNumQueries(4):
            context = report_history.get_patient_examination_history_context(
                self.current
            )
        assert context["previous_examinations"][0]["findings"] == summaries[earlier.pk]

    def test_history_limit_rejects_invalid_values(self) -> None:
        for limit in (0, -1, 51, True):
            with (
                self.subTest(limit=limit),
                pytest.raises(ValueError, match="between 1 and 50"),
            ):
                report_history.get_patient_examination_history_context(
                    self.current, limit=limit
                )
