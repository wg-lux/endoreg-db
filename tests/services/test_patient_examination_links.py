from __future__ import annotations

import pytest
from django.test import TestCase

from endoreg_db.models import (
    Examination,
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
from endoreg_db.services.patient_examination_links import (
    PatientExaminationLinksNotPrefetchedError,
    build_patient_examination_links,
    load_patient_examination_for_links,
)


class PatientExaminationLinksQueryTests(TestCase):
    def setUp(self) -> None:
        patient = Patient.objects.create(
            patient_hash="links-query-patient",
            first_name="Query",
            last_name="Boundary",
        )
        examination = Examination.objects.create(name="links-query-examination")
        self.patient_examination = PatientExamination.objects.create(
            patient=patient,
            examination=examination,
            hash="links-query-patient-examination",
        )
        classification = FindingClassification.objects.create(
            name="links-query-classification"
        )
        choice = FindingClassificationChoice.objects.create(name="links-query-choice")
        classification.choices.add(choice)
        intervention = FindingIntervention.objects.create(
            name="links-query-intervention"
        )

        for index in range(3):
            finding = Finding.objects.create(name=f"links-query-finding-{index}")
            patient_finding = PatientFinding.objects.create(
                patient_examination=self.patient_examination,
                finding=finding,
            )
            PatientFindingClassification.objects.create(
                finding=patient_finding,
                classification=classification,
                classification_choice=choice,
                is_active=True,
            )
            PatientFindingIntervention.objects.create(
                finding=patient_finding,
                intervention=intervention,
                is_active=True,
            )

    def test_builder_fails_loudly_without_canonical_prefetch(self) -> None:
        with self.assertNumQueries(0):
            with pytest.raises(
                PatientExaminationLinksNotPrefetchedError,
                match="load_patient_examination_for_links",
            ):
                build_patient_examination_links(self.patient_examination)

    def test_loader_has_bounded_queries_and_builder_adds_no_queries(self) -> None:
        with self.assertNumQueries(6):
            patient_examination = load_patient_examination_for_links(
                self.patient_examination.pk
            )

        with self.assertNumQueries(0):
            links = build_patient_examination_links(patient_examination)
            assert len(links.patient_findings) == 3
            assert len(links.findings) == 3
            assert len(links.finding_classifications) == 3
            assert len(links.finding_classification_choices) == 3
            assert len(links.finding_interventions) == 3
