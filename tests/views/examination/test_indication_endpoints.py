from __future__ import annotations

# pyright: reportUnknownMemberType=false

from uuid import uuid4

from django.test import TestCase

from endoreg_db.models import (
    Examination,
    ExaminationIndication,
    ExaminationIndicationClassification,
    ExaminationIndicationClassificationChoice,
)


class ExaminationIndicationEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.exam = Examination.objects.create(name=f"exam-indication-{suffix}")
        self.exam_without_indications = Examination.objects.create(
            name=f"exam-empty-{suffix}"
        )

        self.indication = ExaminationIndication.objects.create(
            name=f"indication-main-{suffix}",
            description="Main indication",
        )
        self.indication_secondary = ExaminationIndication.objects.create(
            name=f"indication-secondary-{suffix}",
            description="Secondary indication",
        )
        self.exam.indications.add(self.indication, self.indication_secondary)

        self.classification_a = ExaminationIndicationClassification.objects.create(
            name=f"indication-classification-a-{suffix}"
        )
        self.classification_b = ExaminationIndicationClassification.objects.create(
            name=f"indication-classification-b-{suffix}"
        )
        self.indication.classifications.add(
            self.classification_a, self.classification_b
        )

        self.choice_shared = ExaminationIndicationClassificationChoice.objects.create(
            name=f"indication-choice-shared-{suffix}"
        )
        self.choice_only_a = ExaminationIndicationClassificationChoice.objects.create(
            name=f"indication-choice-only-a-{suffix}"
        )
        self.choice_other_indication = (
            ExaminationIndicationClassificationChoice.objects.create(
                name=f"indication-choice-other-{suffix}"
            )
        )

        self.classification_a.choices.add(self.choice_shared, self.choice_only_a)
        self.classification_b.choices.add(self.choice_shared)

        classification_other = ExaminationIndicationClassification.objects.create(
            name=f"indication-classification-other-{suffix}"
        )
        self.indication_secondary.classifications.add(classification_other)
        classification_other.choices.add(self.choice_other_indication)

    def test_get_indications_for_examination_returns_linked_indications(self):
        response = self.client.get(f"/api/examinations/{self.exam.pk}/indications/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert len(payload) == 2
        assert all(
            set(item.keys()) == {"id", "name", "name_de", "name_en", "description"}
            for item in payload
        )
        returned_ids = {item["id"] for item in payload}
        assert returned_ids == {self.indication.pk, self.indication_secondary.pk}
        localized = next(item for item in payload if item["id"] == self.indication.pk)
        assert localized["name_de"] == self.indication.name
        assert localized["name_en"] == self.indication.name
        fallback = next(
            item for item in payload if item["id"] == self.indication_secondary.pk
        )
        assert fallback["name_de"] == self.indication_secondary.name
        assert fallback["name_en"] == self.indication_secondary.name

    def test_get_indications_for_examination_returns_empty_list_when_unlinked(self):
        response = self.client.get(
            f"/api/examinations/{self.exam_without_indications.pk}/indications/"
        )

        assert response.status_code == 200, response.content
        assert response.json() == []

    def test_get_indications_for_examination_returns_404_for_unknown_exam(self):
        response = self.client.get("/api/examinations/999999/indications/")
        assert response.status_code == 404, response.content

    def test_get_indication_choices_returns_distinct_choices_for_selected_indication(
        self,
    ):
        response = self.client.get(f"/api/indications/{self.indication.pk}/choices/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert len(payload) == 2
        row_by_id = {item["id"]: item for item in payload}

        assert row_by_id[self.choice_only_a.pk] == {
            "id": self.choice_only_a.pk,
            "name": self.choice_only_a.name,
            "name_de": self.choice_only_a.name,
            "name_en": self.choice_only_a.name,
            "classification_ids": [self.classification_a.pk],
        }
        assert row_by_id[self.choice_shared.pk] == {
            "id": self.choice_shared.pk,
            "name": self.choice_shared.name,
            "name_de": self.choice_shared.name,
            "name_en": self.choice_shared.name,
            "classification_ids": sorted(
                [self.classification_a.pk, self.classification_b.pk]
            ),
        }
        assert self.choice_other_indication.pk not in row_by_id

    def test_get_indication_choices_returns_empty_list_for_indication_without_classifications(
        self,
    ):
        suffix = uuid4().hex[:8]
        indication_without_choices = ExaminationIndication.objects.create(
            name=f"indication-no-choices-{suffix}"
        )

        response = self.client.get(
            f"/api/indications/{indication_without_choices.pk}/choices/"
        )
        assert response.status_code == 200, response.content
        assert response.json() == []

    def test_get_indication_choices_returns_404_for_unknown_indication(self):
        response = self.client.get("/api/indications/999999/choices/")
        assert response.status_code == 404, response.content
