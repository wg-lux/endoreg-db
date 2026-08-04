from __future__ import annotations

from uuid import uuid4

from django.test import TestCase

from endoreg_db.models import Examination, Finding, FindingIntervention


class ExaminationInterventionEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]

        self.exam = Examination.objects.create(name=f"exam-intervention-{suffix}")
        self.exam_empty = Examination.objects.create(name=f"exam-empty-{suffix}")
        self.other_exam = Examination.objects.create(name=f"exam-other-{suffix}")

        self.finding_a = Finding.objects.create(name=f"finding-a-{suffix}")
        self.finding_b = Finding.objects.create(name=f"finding-b-{suffix}")
        self.finding_other = Finding.objects.create(name=f"finding-other-{suffix}")

        self.exam.findings.add(self.finding_a, self.finding_b)
        self.other_exam.findings.add(self.finding_other)

        self.intervention_a = FindingIntervention.objects.create(
            name=f"intervention-a-{suffix}"
        )
        self.intervention_b = FindingIntervention.objects.create(
            name=f"intervention-b-{suffix}"
        )
        self.intervention_c = FindingIntervention.objects.create(
            name=f"intervention-c-{suffix}"
        )
        self.intervention_other = FindingIntervention.objects.create(
            name=f"intervention-other-{suffix}"
        )

        self.finding_a.finding_interventions.add(
            self.intervention_a, self.intervention_b
        )
        self.finding_b.finding_interventions.add(
            self.intervention_b, self.intervention_c
        )
        self.finding_other.finding_interventions.add(self.intervention_other)

    def test_get_interventions_for_examination_returns_distinct_interventions(self):
        response = self.client.get(f"/api/examinations/{self.exam.id}/interventions/")
        assert response.status_code == 200, response.content

        payload = response.json()
        returned_ids = {item["id"] for item in payload}
        assert returned_ids == {
            self.intervention_a.id,
            self.intervention_b.id,
            self.intervention_c.id,
        }
        assert all(set(item.keys()) == {"id", "name"} for item in payload)

    def test_get_interventions_for_examination_returns_empty_list(self):
        response = self.client.get(
            f"/api/examinations/{self.exam_empty.id}/interventions/"
        )
        assert response.status_code == 200, response.content
        assert response.json() == []

    def test_get_interventions_for_examination_returns_404_for_unknown_exam(self):
        response = self.client.get("/api/examinations/999999/interventions/")
        assert response.status_code == 404, response.content

    def test_get_interventions_for_finding_returns_finding_interventions(self):
        response = self.client.get(
            f"/api/examinations/{self.exam.id}/findings/{self.finding_a.id}/interventions/"
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        returned_ids = {item["id"] for item in payload}
        assert returned_ids == {self.intervention_a.id, self.intervention_b.id}
        assert all(set(item.keys()) == {"id", "name"} for item in payload)

    def test_get_interventions_for_finding_returns_404_when_finding_not_in_exam(self):
        response = self.client.get(
            f"/api/examinations/{self.exam.id}/findings/{self.finding_other.id}/interventions/"
        )
        assert response.status_code == 404, response.content
        assert response.json() == {"error": "Finding not found for this examination"}
