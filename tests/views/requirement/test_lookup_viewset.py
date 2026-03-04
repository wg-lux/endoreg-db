from __future__ import annotations

import json

import pytest
from django.core.cache import cache
from django.test import TestCase

from endoreg_db.services.lookup_store import DEFAULT_TTL_SECONDS


class LookupViewSetContractTests(TestCase):
    def setUp(self):
        cache.clear()

    def _seed_lookup_session(self, token: str, *, patient_examination_id: int = 123):
        cache.set(
            f"lookup:{token}",
            {
                "patient_examination_id": patient_examination_id,
                "requirements_by_set": {},
                "requirement_status": {},
                "selected_requirement_set_ids": [],
                "selected_choices": {},
            },
            DEFAULT_TTL_SECONDS,
        )

    def test_init_returns_token_for_valid_payload(self):
        from endoreg_db.views.requirement import lookup as view_module

        captured_patient_examination_ids: list[int] = []
        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.LookupViewSet,
            "_issue_lookup_token",
            lambda self, init_payload: (
                captured_patient_examination_ids.append(
                    init_payload.patient_examination_id
                )
                or "issued-token"
            ),
        )

        try:
            resp = self.client.post(
                "/api/lookup/init/",
                data=json.dumps({"patient_examination_id": 123}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 201, resp.content
        assert resp.json() == {"token": "issued-token"}
        assert captured_patient_examination_ids == [123]

    def test_init_accepts_query_param_patient_examination_id(self):
        from endoreg_db.views.requirement import lookup as view_module

        captured_patient_examination_ids: list[int] = []
        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.LookupViewSet,
            "_issue_lookup_token",
            lambda self, init_payload: (
                captured_patient_examination_ids.append(
                    init_payload.patient_examination_id
                )
                or "issued-from-query"
            ),
        )

        try:
            resp = self.client.post(
                "/api/lookup/init/?patient_examination_id=42",
                data=json.dumps({}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 201, resp.content
        assert resp.json()["token"] == "issued-from-query"
        assert captured_patient_examination_ids == [42]

    def test_init_accepts_single_key_legacy_form_payload(self):
        from endoreg_db.views.requirement import lookup as view_module

        captured_patient_examination_ids: list[int] = []
        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.LookupViewSet,
            "_issue_lookup_token",
            lambda self, init_payload: (
                captured_patient_examination_ids.append(
                    init_payload.patient_examination_id
                )
                or "issued-from-legacy-form"
            ),
        )

        try:
            resp = self.client.post(
                "/api/lookup/init/",
                data={"{'patient_examination_id': 77}": "1"},
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 201, resp.content
        assert resp.json()["token"] == "issued-from-legacy-form"
        assert captured_patient_examination_ids == [77]

    def test_init_rejects_invalid_patient_examination_id(self):
        resp = self.client.post(
            "/api/lookup/init/",
            data=json.dumps({"patient_examination_id": "abc"}),
            content_type="application/json",
        )

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_init_invalid_patient_examination_id"
        assert payload["lifecycle"] == "init"
        assert payload["lifecycle_contract"] == "init -> all/parts -> recompute"

    def test_init_returns_lookup_init_error_when_issuance_throws(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.LookupViewSet,
            "_issue_lookup_token",
            lambda self, init_payload: (_ for _ in ()).throw(
                RuntimeError("forced-init-failure")
            ),
        )

        try:
            resp = self.client.post(
                "/api/lookup/init/",
                data=json.dumps({"patient_examination_id": 123}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_init_failed"
        assert "forced-init-failure" in payload["detail"]
        assert payload["lifecycle"] == "init"

    def test_init_issues_internal_then_public_token_on_repeated_calls(self):
        from endoreg_db.views.requirement import lookup as view_module

        def _fake_create_lookup_token_for_pe(pe_id, **kwargs):
            cache.set(
                "lookup:internal-fixed-token",
                {
                    "patient_examination_id": pe_id,
                    "requirements_by_set": {},
                    "requirement_status": {},
                },
                DEFAULT_TTL_SECONDS,
            )
            return "internal-fixed-token"

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "create_lookup_token_for_pe",
            _fake_create_lookup_token_for_pe,
        )

        try:
            first = self.client.post(
                "/api/lookup/init/",
                data=json.dumps({"patient_examination_id": 11}),
                content_type="application/json",
            )
            second = self.client.post(
                "/api/lookup/init/",
                data=json.dumps({"patient_examination_id": 11}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert first.status_code == 201, first.content
        assert second.status_code == 201, second.content

        first_token = first.json()["token"]
        second_token = second.json()["token"]
        assert first_token == "internal-fixed-token"
        assert second_token != first_token

        mirrored_data = cache.get(f"lookup:{second_token}") or {}
        assert mirrored_data.get("patient_examination_id") == 11

    def test_recompute_without_token_reinitializes_from_patient_examination_id(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.LookupViewSet,
            "_issue_lookup_token",
            lambda self, init_payload: "token-from-reinit",
        )
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: {
                "requirements_by_set": {},
                "requirement_status": {},
                "requirement_set_status": {},
            },
        )

        try:
            resp = self.client.post(
                "/api/lookup/recompute/",
                data=json.dumps({"patient_examination_id": 123}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 200, resp.content
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["token"] == "token-from-reinit"
        assert "updates" in payload

    def test_recompute_without_token_requires_patient_examination_id(self):
        resp = self.client.post(
            "/api/lookup/recompute/",
            data=json.dumps({}),
            content_type="application/json",
        )

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert (
            payload["error_code"] == "lookup_recompute_patient_examination_id_required"
        )
        assert payload["lifecycle"] == "recompute"

    def test_recompute_without_token_rejects_non_integer_patient_examination_id(self):
        resp = self.client.post(
            "/api/lookup/recompute/",
            data=json.dumps({"patient_examination_id": "abc"}),
            content_type="application/json",
        )

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert (
            payload["error_code"] == "lookup_recompute_patient_examination_id_required"
        )
        assert payload["lifecycle"] == "recompute"

    def test_recompute_without_token_returns_invalid_state_on_value_error(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.LookupViewSet,
            "_issue_lookup_token",
            lambda self, init_payload: "token-for-invalid-state",
        )
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: (_ for _ in ()).throw(ValueError("forced-invalid-state")),
        )

        try:
            resp = self.client.post(
                "/api/lookup/recompute/",
                data=json.dumps({"patient_examination_id": 123}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_recompute_invalid_state"
        assert payload["lifecycle"] == "recompute"

    def test_recompute_without_token_returns_error_on_exception(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.LookupViewSet,
            "_issue_lookup_token",
            lambda self, init_payload: "token-for-failure",
        )
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: (_ for _ in ()).throw(RuntimeError("forced-recompute-fail")),
        )

        try:
            resp = self.client.post(
                "/api/lookup/recompute/",
                data=json.dumps({"patient_examination_id": 123}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_recompute_failed"
        assert payload["lifecycle"] == "recompute"

    def test_get_all_returns_state_for_existing_session(self):
        self._seed_lookup_session("existing-token", patient_examination_id=9001)

        resp = self.client.get("/api/lookup/existing-token/all/")

        assert resp.status_code == 200, resp.content
        payload = resp.json()
        assert payload["patient_examination_id"] == 9001

    def test_get_all_recovers_session_using_origin_map(self):
        from endoreg_db.views.requirement import lookup as view_module

        cache.set("lookup:origin:expired-token", 321, DEFAULT_TTL_SECONDS)
        self._seed_lookup_session(
            "internal-recovered-token", patient_examination_id=321
        )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "create_lookup_token_for_pe",
            lambda pe_id: "internal-recovered-token",
        )

        try:
            resp = self.client.get("/api/lookup/expired-token/all/")
        finally:
            monkeypatches.undo()

        assert resp.status_code == 200, resp.content
        payload = resp.json()
        assert payload["patient_examination_id"] == 321

        mirrored_data = cache.get("lookup:expired-token") or {}
        assert mirrored_data.get("patient_examination_id") == 321

    def test_get_all_returns_restart_missing_data_when_recovery_has_no_data(self):
        from endoreg_db.views.requirement import lookup as view_module

        cache.set("lookup:origin:expired-token", 555, DEFAULT_TTL_SECONDS)

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "create_lookup_token_for_pe",
            lambda pe_id: "internal-empty-token",
        )

        try:
            resp = self.client.get("/api/lookup/expired-token/all/")
        finally:
            monkeypatches.undo()

        assert resp.status_code == 404, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_data_unavailable_after_restart"
        assert payload["token"] == "expired-token"
        assert payload["lifecycle"] == "all"

    def test_get_all_returns_session_not_found_without_origin_mapping(self):
        resp = self.client.get("/api/lookup/missing-token/all/")

        assert resp.status_code == 404, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_session_not_found"
        assert payload["token"] == "missing-token"
        assert payload["lifecycle"] == "all"

    def test_parts_get_returns_requested_keys_for_existing_session(self):
        self._seed_lookup_session("parts-token", patient_examination_id=44)
        cache.set(
            "lookup:parts-token",
            {
                "patient_examination_id": 44,
                "requirements_by_set": {"1": [{"id": 10, "name": "req-1"}]},
                "requirement_status": {"10": True},
                "required_findings": [7, 8],
            },
            DEFAULT_TTL_SECONDS,
        )

        resp = self.client.get(
            "/api/lookup/parts-token/parts/?keys=required_findings,requirement_status"
        )

        assert resp.status_code == 200, resp.content
        payload = resp.json()
        assert payload["required_findings"] == [7, 8]
        assert payload["requirement_status"] == {"10": True}

    def test_parts_get_requires_keys_with_standardized_400_payload(self):
        resp = self.client.get("/api/lookup/example-token/parts/")

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_parts_keys_required"
        assert payload["token"] == "example-token"
        assert payload["lifecycle"] == "parts"

    def test_parts_get_returns_404_for_missing_session_when_keys_provided(self):
        resp = self.client.get(
            "/api/lookup/missing-session-token/parts/?keys=requirement_status"
        )

        assert resp.status_code == 404, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_session_not_found"
        assert payload["token"] == "missing-session-token"
        assert payload["lifecycle"] == "parts"

    def test_parts_patch_rejects_invalid_updates_payload(self):
        resp = self.client.patch(
            "/api/lookup/parts-token/parts/",
            data=json.dumps({}),
            content_type="application/json",
        )

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_parts_invalid_updates"
        assert payload["token"] == "parts-token"
        assert payload["lifecycle"] == "parts"

    def test_parts_patch_returns_404_for_missing_session(self):
        resp = self.client.patch(
            "/api/lookup/missing-patch-token/parts/",
            data=json.dumps(
                {"updates": {"selected_choices": {"10": {"choice_id": 3}}}}
            ),
            content_type="application/json",
        )

        assert resp.status_code == 404, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_session_not_found"
        assert payload["token"] == "missing-patch-token"
        assert payload["lifecycle"] == "parts"

    def test_parts_patch_updates_existing_session_without_recompute_for_non_input_keys(
        self,
    ):
        from endoreg_db.views.requirement import lookup as view_module

        self._seed_lookup_session("parts-no-recompute-token", patient_examination_id=12)
        recompute_calls: list[str] = []

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: recompute_calls.append(token),
        )

        try:
            resp = self.client.patch(
                "/api/lookup/parts-no-recompute-token/parts/",
                data=json.dumps({"updates": {"requirement_status": {"77": True}}}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 200, resp.content
        assert recompute_calls == []
        cached_data = cache.get("lookup:parts-no-recompute-token") or {}
        assert cached_data.get("requirement_status") == {"77": True}

    def test_parts_patch_triggers_recompute_for_input_keys(self):
        from endoreg_db.views.requirement import lookup as view_module

        self._seed_lookup_session("parts-recompute-token", patient_examination_id=12)
        recompute_calls: list[str] = []

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: recompute_calls.append(token),
        )

        try:
            resp = self.client.patch(
                "/api/lookup/parts-recompute-token/parts/",
                data=json.dumps({"updates": {"selected_requirement_set_ids": [1, 2]}}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 200, resp.content
        assert recompute_calls == ["parts-recompute-token"]

    def test_parts_patch_returns_ok_when_recompute_errors(self):
        from endoreg_db.views.requirement import lookup as view_module

        self._seed_lookup_session(
            "parts-recompute-error-token", patient_examination_id=12
        )

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: (_ for _ in ()).throw(RuntimeError("forced-recompute-error")),
        )

        try:
            resp = self.client.patch(
                "/api/lookup/parts-recompute-error-token/parts/",
                data=json.dumps(
                    {"updates": {"selected_choices": {"10": {"choice_id": 7}}}}
                ),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 200, resp.content
        assert resp.json()["ok"] is True

    def test_token_recompute_returns_updates_payload(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: {
                "requirements_by_set": {},
                "requirement_status": {"1": True},
                "requirement_set_status": {"9": True},
            },
        )

        try:
            resp = self.client.post(
                "/api/lookup/token-recompute-success/recompute/",
                data=json.dumps({}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 200, resp.content
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["token"] == "token-recompute-success"
        assert payload["updates"]["requirement_status"] == {"1": True}

    def test_token_recompute_missing_session_returns_standardized_404_payload(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: (_ for _ in ()).throw(
                ValueError(f"No lookup data found for token {token}")
            ),
        )

        try:
            resp = self.client.post(
                "/api/lookup/missing-token/recompute/",
                data=json.dumps({}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 404, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_session_not_found"
        assert payload["token"] == "missing-token"
        assert payload["lifecycle"] == "recompute"

    def test_token_recompute_returns_invalid_state_for_other_value_errors(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: (_ for _ in ()).throw(
                ValueError(
                    "Invalid lookup data for token X: patient_examination_id is empty"
                )
            ),
        )

        try:
            resp = self.client.post(
                "/api/lookup/token-invalid-state/recompute/",
                data=json.dumps({}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_recompute_invalid_state"
        assert payload["token"] == "token-invalid-state"
        assert payload["lifecycle"] == "recompute"

    def test_token_recompute_returns_error_for_exceptions(self):
        from endoreg_db.views.requirement import lookup as view_module

        monkeypatches = pytest.MonkeyPatch()
        monkeypatches.setattr(
            view_module.ls,
            "recompute_lookup",
            lambda token: (_ for _ in ()).throw(
                RuntimeError("forced-unexpected-error")
            ),
        )

        try:
            resp = self.client.post(
                "/api/lookup/token-generic-error/recompute/",
                data=json.dumps({}),
                content_type="application/json",
            )
        finally:
            monkeypatches.undo()

        assert resp.status_code == 400, resp.content
        payload = resp.json()
        assert payload["ok"] is False
        assert payload["error_code"] == "lookup_recompute_failed"
        assert payload["token"] == "token-generic-error"
        assert payload["lifecycle"] == "recompute"
