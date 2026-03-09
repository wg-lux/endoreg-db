from __future__ import annotations

from uuid import uuid4

from django.test import TestCase

from endoreg_db.models import Center, EndoscopyProcessor


class ApplicationSettingsEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"settings-center-{suffix}")
        self.processor = EndoscopyProcessor.objects.create(
            name=f"settings-processor-{suffix}",
            image_width=1920,
            image_height=1080,
            endoscope_image_x=0,
            endoscope_image_y=0,
            endoscope_image_width=0,
            endoscope_image_height=0,
            examination_date_x=0,
            examination_date_y=0,
            examination_date_width=0,
            examination_date_height=0,
            patient_first_name_x=0,
            patient_first_name_y=0,
            patient_first_name_width=0,
            patient_first_name_height=0,
            patient_last_name_x=0,
            patient_last_name_y=0,
            patient_last_name_width=0,
            patient_last_name_height=0,
            patient_dob_x=0,
            patient_dob_y=0,
            patient_dob_width=0,
            patient_dob_height=0,
        )

    def test_get_application_settings(self):
        response = self.client.get("/api/settings/application/")
        assert response.status_code == 200, response.content

        payload = response.json()
        assert set(payload.keys()) >= {
            "id",
            "center_id",
            "center_name",
            "processor_id",
            "processor_name",
            "annotator_name",
            "report_template_name",
            "updated_at",
        }

    def test_patch_application_settings_with_valid_ids(self):
        response = self.client.patch(
            "/api/settings/application/",
            data={
                "center_id": self.center.pk,
                "processor_id": self.processor.pk,
                "annotator_name": "annotator_a",
                "report_template_name": "template_a",
            },
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["center_id"] == self.center.pk
        assert payload["processor_id"] == self.processor.pk
        assert payload["annotator_name"] == "annotator_a"
        assert payload["report_template_name"] == "template_a"

    def test_patch_application_settings_rejects_unknown_center(self):
        response = self.client.patch(
            "/api/settings/application/",
            data={"center_id": 999999},
            content_type="application/json",
        )
        assert response.status_code == 400, response.content
        assert "center" in response.json()["errors"]

    def test_application_settings_dropdown_endpoints(self):
        centers_response = self.client.get(
            "/api/settings/application/dropdowns/centers/"
        )
        assert centers_response.status_code == 200, centers_response.content
        assert any(
            entry["id"] == self.center.pk and entry["name"] == self.center.name
            for entry in centers_response.json()
        )

        processors_response = self.client.get(
            "/api/settings/application/dropdowns/processors/"
        )
        assert processors_response.status_code == 200, processors_response.content
        assert any(
            entry["id"] == self.processor.pk and entry["name"] == self.processor.name
            for entry in processors_response.json()
        )

        annotators_response = self.client.get(
            "/api/settings/application/dropdowns/annotators/"
        )
        assert annotators_response.status_code == 200, annotators_response.content
        assert isinstance(annotators_response.json(), list)

        templates_response = self.client.get(
            "/api/settings/application/dropdowns/report_templates/"
        )
        assert templates_response.status_code == 200, templates_response.content
        assert isinstance(templates_response.json(), list)
