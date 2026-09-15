from __future__ import annotations

# pyright: reportUnknownMemberType=false


from django.contrib.auth.models import Group, User
from django.test import TestCase
from endoreg_db.helpers.model_ids import model_pk, model_fk

from endoreg_db.models import Center, Gender, Patient, PortalUserInfo


class PatientCenterContractTests(TestCase):
    def setUp(self) -> None:
        self.gender = Gender.objects.create(name="female")
        self.center = Center.objects.create(
            name="internal-center-name",
            display_name="Display Center",
        )

    def test_centers_endpoint_exposes_center_key_and_display_name(self) -> None:
        response = self.client.get("/api/centers/")

        assert response.status_code == 200, response.content
        payload = response.json()
        assert len(payload) >= 1

        center_id = model_pk(self.center)
        center_payload = next(item for item in payload if item["id"] == center_id)

        assert center_payload["center_key"] == self.center.center_key
        assert center_payload["name"] == "Display Center"
        assert center_payload["display_name"] == "Display Center"

    def test_patients_endpoint_accepts_center_key_for_create(self) -> None:
        response = self.client.post(
            "/api/patients/",
            data={
                "first_name": "Clara",
                "last_name": "Contract",
                "gender": self.gender.name,
                "center_key": self.center.center_key,
                "is_real_person": False,
            },
            content_type="application/json",
        )

        assert response.status_code == 201, response.content
        payload = response.json()

        assert payload["center_key"] == self.center.center_key
        assert payload["center"] == "Display Center"

        patient = Patient.objects.get(pk=payload["id"])
        assert model_fk(patient, "center") == model_pk(self.center)

    def test_patient_create_hides_foreign_center_existence(self) -> None:
        own_center = Center.objects.create(name="patient-writer-own-center")
        user = User.objects.create_user(username="patient-writer")
        user.groups.add(Group.objects.create(name="patient:write"))
        portal_info = PortalUserInfo.objects.create(user=user)
        portal_info.centers.add(own_center)
        self.client.force_login(user)

        response = self.client.post(
            "/api/patients/",
            data={
                "first_name": "Foreign",
                "last_name": "Center",
                "gender": self.gender.name,
                "center_key": self.center.center_key,
                "is_real_person": False,
            },
            content_type="application/json",
        )

        assert response.status_code == 404, response.content
        assert not Patient.objects.filter(first_name="Foreign").exists()

    def test_patients_endpoint_rejects_legacy_center_name_write(self) -> None:
        response = self.client.post(
            "/api/patients/",
            data={
                "first_name": "Nina",
                "last_name": "Legacy",
                "gender": self.gender.name,
                "center": self.center.display_name,
                "is_real_person": False,
            },
            content_type="application/json",
        )

        assert response.status_code == 400, response.content
        payload = response.json()

        assert "center_key" in payload
        assert "canonical center identifier" in payload["center_key"][0]
