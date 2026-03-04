from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase


class EvaluateRequirementsViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="evaluate_requirements_user",
            password="password",
        )
        self.client.force_login(self.user)

    def test_returns_error_payload_when_patient_examination_id_missing(self):
        response = self.client.post(
            "/api/evaluate-requirements/",
            data=json.dumps({}),
            content_type="application/json",
        )

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["ok"] is False
        assert payload["errors"] == ["patient_examination_id is required"]
        assert payload["meta"]["patient_examination_id"] is None
        assert payload["meta"]["sets_evaluated"] == 0
        assert payload["meta"]["requirements_evaluated"] == 0
        assert payload["meta"]["status"] == "failed"
        assert "patientExaminationId" not in payload["meta"]

    def test_evaluates_from_dtypes_guidance_payload(self):
        from endoreg_db.views.requirement import evaluate as view_module

        guidance_payload = {
            "requirements_by_set": {
                "1001": [
                    {"id": 2001, "name": "findings_validator:quality"},
                    {"id": 2002, "name": "findings_validator:lesion"},
                ]
            },
            "requirement_status": {"2001": True, "2002": False},
            "requirement_set_status": {"1001": False},
            "suggested_actions": {"2002": [{"note": "validator_unsatisfied"}]},
        }

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: SimpleNamespace(id=pe_id, examination=None),
        )
        monkeypatches.setattr(
            view_module.lookup_service,
            "evaluate_patient_exam_requirement_guidance",
            lambda pe, selected_requirement_set_ids=None: guidance_payload,
        )

        try:
            response = self.client.post(
                "/api/evaluate-requirements/",
                data=json.dumps({"patient_examination_id": 123}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["ok"] is True
        assert payload["errors"] == []
        assert payload["meta"]["patient_examination_id"] == 123
        assert payload["meta"]["sets_evaluated"] == 1
        assert payload["meta"]["requirements_evaluated"] == 2
        assert payload["meta"]["status"] == "ok"

        assert payload["results"][0]["status"] == "PASSED"
        assert payload["results"][0]["met"] is True
        assert payload["results"][1]["status"] == "FAILED"
        assert payload["results"][1]["met"] is False
        assert payload["results"][1]["details"] == "validator_unsatisfied"

    def test_selected_requirement_set_ids_filter_results(self):
        from endoreg_db.views.requirement import evaluate as view_module

        guidance_payload = {
            "requirements_by_set": {
                "1001": [{"id": 2001, "name": "findings_validator:a"}],
                "1002": [{"id": 2002, "name": "findings_validator:b"}],
            },
            "requirement_status": {"2001": True, "2002": False},
            "requirement_set_status": {"1001": True, "1002": False},
            "suggested_actions": {"2002": [{"note": "missing"}]},
        }

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: SimpleNamespace(id=pe_id, examination=None),
        )
        monkeypatches.setattr(
            view_module.lookup_service,
            "evaluate_patient_exam_requirement_guidance",
            lambda pe, selected_requirement_set_ids=None: guidance_payload,
        )

        try:
            response = self.client.post(
                "/api/evaluate-requirements/",
                data=json.dumps(
                    {
                        "patient_examination_id": 123,
                        "requirement_set_ids": [1002],
                    }
                ),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["ok"] is True
        assert payload["meta"]["sets_evaluated"] == 1
        assert payload["meta"]["requirements_evaluated"] == 1
        assert len(payload["results"]) == 1
        assert payload["results"][0]["requirement_set_id"] == 1002

    def test_returns_error_when_selected_requirement_sets_are_unknown(self):
        from endoreg_db.views.requirement import evaluate as view_module

        guidance_payload = {
            "requirements_by_set": {
                "1001": [{"id": 2001, "name": "findings_validator:a"}],
            },
            "requirement_status": {"2001": True},
            "requirement_set_status": {"1001": True},
            "suggested_actions": {},
        }

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: SimpleNamespace(id=pe_id, examination=None),
        )
        monkeypatches.setattr(
            view_module.lookup_service,
            "evaluate_patient_exam_requirement_guidance",
            lambda pe, selected_requirement_set_ids=None: guidance_payload,
        )

        try:
            response = self.client.post(
                "/api/evaluate-requirements/",
                data=json.dumps(
                    {
                        "patient_examination_id": 123,
                        "requirement_set_ids": [9999],
                    }
                ),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["ok"] is False
        assert payload["meta"]["status"] == "failed"
        assert payload["meta"]["sets_evaluated"] == 0
        assert payload["results"] == []
        assert payload["errors"] == ["No RequirementSets found for IDs: [9999]"]
