from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.test import TestCase

from endoreg_db.services import lookup_service


class _FakeRelatedManager:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class LookupServiceRequirementSourceTests(TestCase):
    def _build_fake_patient_exam(self):
        return SimpleNamespace(
            id=123,
            examination=SimpleNamespace(name="colonoscopy"),
            patient_findings=_FakeRelatedManager([]),
        )

    def test_evaluate_guidance_prefers_dtypes_when_adapter_returns_data(self):
        pe = self._build_fake_patient_exam()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "dtypes",
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_requirement_guidance",
            lambda **kwargs: {"engine": "dtypes", "ok": True},
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("legacy fallback should not run when dtypes data exists")
            ),
        )
        try:
            result = lookup_service.evaluate_patient_exam_requirement_guidance(pe)
        finally:
            monkeypatch.undo()

        assert result == {"engine": "dtypes", "ok": True}

    def test_evaluate_guidance_errors_when_dtypes_missing_and_legacy_fallback_disabled(
        self,
    ):
        pe = self._build_fake_patient_exam()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "dtypes",
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_legacy_fallback_enabled",
            lambda: False,
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_requirement_guidance",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            lookup_service,
            "propose_candidate_requirement_sets",
            lambda **kwargs: SimpleNamespace(
                candidate_requirement_set_ids=[],
                confidence=0.0,
            ),
        )
        monkeypatch.setattr(
            lookup_service,
            "get_patient_examination_history_context",
            lambda *args, **kwargs: {},
        )
        with pytest.raises(
            ValueError,
            match="LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED is false",
        ):
            try:
                lookup_service.evaluate_patient_exam_requirement_guidance(pe)
            finally:
                monkeypatch.undo()

    def test_evaluate_guidance_falls_back_to_legacy_when_emergency_flag_enabled(self):
        pe = self._build_fake_patient_exam()
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "dtypes",
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_legacy_fallback_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_requirement_guidance",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            lookup_service,
            "propose_candidate_requirement_sets",
            lambda **kwargs: SimpleNamespace(
                candidate_requirement_set_ids=[],
                confidence=0.0,
            ),
        )
        monkeypatch.setattr(
            lookup_service,
            "get_patient_examination_history_context",
            lambda *args, **kwargs: {},
        )
        try:
            result = lookup_service.evaluate_patient_exam_requirement_guidance(pe)
        finally:
            monkeypatch.undo()

        assert result["advisory_only"] is True
        assert result["requirement_status"] == {}
        assert result["requirement_set_status"] == {}

    def test_recompute_prefers_dtypes_updates_when_present(self):
        pe = self._build_fake_patient_exam()

        class _FakeStore:
            def __init__(self):
                self.data = {
                    "patient_examination_id": 123,
                    "selected_requirement_set_ids": [],
                    "selected_choices": {},
                }
                self.recompute_done = False

            def get_all(self):
                return dict(self.data)

            def set(self, key, value):
                self.data[key] = value

            def validate_and_recover_data(self, token):
                assert token == "tok"
                return dict(self.data)

            def get_many(self, keys):
                return {key: self.data.get(key) for key in keys}

            def set_many(self, updates):
                self.data.update(updates)

            def mark_recompute_done(self):
                self.recompute_done = True

        fake_store = _FakeStore()
        dtypes_updates = {
            "requirements_by_set": {"1001": [{"id": 2001, "name": "validator:ok"}]},
            "requirement_status": {"2001": True},
            "requirement_set_status": {"1001": True},
            "requirement_defaults": {},
            "classification_choices": {},
            "suggested_actions": {},
            "candidate_requirement_set_ids": [1001],
            "candidate_requirement_set_confidence": 1.0,
        }

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "LookupStore",
            lambda *args, **kwargs: fake_store,
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "dtypes",
        )
        monkeypatch.setattr(
            lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: pe,
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            lookup_service,
            "get_patient_examination_history_context",
            lambda *args, **kwargs: {},
        )
        monkeypatch.setattr(
            lookup_service,
            "propose_candidate_requirement_sets",
            lambda **kwargs: SimpleNamespace(
                candidate_requirement_set_ids=[],
                confidence=0.0,
            ),
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_lookup_updates",
            lambda **kwargs: dtypes_updates,
        )
        try:
            result = lookup_service.recompute_lookup("tok")
        finally:
            monkeypatch.undo()

        assert result == dtypes_updates
        assert fake_store.recompute_done is True
        assert fake_store.data["requirement_set_status"] == {"1001": True}

    def test_recompute_errors_when_dtypes_missing_and_legacy_fallback_disabled(self):
        pe = self._build_fake_patient_exam()

        class _FakeStore:
            def __init__(self):
                self.data = {
                    "patient_examination_id": 123,
                    "selected_requirement_set_ids": [],
                    "selected_choices": {},
                }
                self.recompute_done = False

            def get_all(self):
                return dict(self.data)

            def set(self, key, value):
                self.data[key] = value

            def validate_and_recover_data(self, token):
                assert token == "tok"
                return dict(self.data)

            def get_many(self, keys):
                return {key: self.data.get(key) for key in keys}

            def set_many(self, updates):
                self.data.update(updates)

            def mark_recompute_done(self):
                self.recompute_done = True

        fake_store = _FakeStore()

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "LookupStore",
            lambda *args, **kwargs: fake_store,
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "dtypes",
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_legacy_fallback_enabled",
            lambda: False,
        )
        monkeypatch.setattr(
            lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: pe,
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            lookup_service,
            "get_patient_examination_history_context",
            lambda *args, **kwargs: {},
        )
        monkeypatch.setattr(
            lookup_service,
            "propose_candidate_requirement_sets",
            lambda **kwargs: SimpleNamespace(
                candidate_requirement_set_ids=[],
                confidence=0.0,
            ),
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_lookup_updates",
            lambda **kwargs: None,
        )

        with pytest.raises(
            ValueError,
            match="LOOKUP_REQUIREMENT_LEGACY_FALLBACK_ENABLED is false",
        ):
            try:
                lookup_service.recompute_lookup("tok")
            finally:
                monkeypatch.undo()

        assert fake_store.recompute_done is False

    def test_recompute_falls_back_to_legacy_when_emergency_flag_enabled(self):
        pe = self._build_fake_patient_exam()

        class _FakeStore:
            def __init__(self):
                self.data = {
                    "patient_examination_id": 123,
                    "selected_requirement_set_ids": [],
                    "selected_choices": {},
                }
                self.recompute_done = False

            def get_all(self):
                return dict(self.data)

            def set(self, key, value):
                self.data[key] = value

            def validate_and_recover_data(self, token):
                assert token == "tok"
                return dict(self.data)

            def get_many(self, keys):
                return {key: self.data.get(key) for key in keys}

            def set_many(self, updates):
                self.data.update(updates)

            def mark_recompute_done(self):
                self.recompute_done = True

        fake_store = _FakeStore()
        legacy_updates = {
            "requirements_by_set": {"1001": [{"id": 2001, "name": "legacy"}]},
            "requirement_status": {"2001": False},
            "requirement_set_status": {"1001": False},
            "requirement_defaults": {},
            "classification_choices": {},
            "suggested_actions": {},
            "candidate_requirement_set_ids": [],
            "candidate_requirement_set_confidence": 0.0,
        }

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "LookupStore",
            lambda *args, **kwargs: fake_store,
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "dtypes",
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_legacy_fallback_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: pe,
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            lookup_service,
            "get_patient_examination_history_context",
            lambda *args, **kwargs: {},
        )
        monkeypatch.setattr(
            lookup_service,
            "propose_candidate_requirement_sets",
            lambda **kwargs: SimpleNamespace(
                candidate_requirement_set_ids=[],
                confidence=0.0,
            ),
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_lookup_updates",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            lookup_service,
            "_build_legacy_lookup_updates",
            lambda **kwargs: legacy_updates,
        )
        try:
            result = lookup_service.recompute_lookup("tok")
        finally:
            monkeypatch.undo()

        assert result == legacy_updates
        assert fake_store.recompute_done is True

    def test_evaluate_guidance_hybrid_compare_logs_divergence_and_returns_dtypes(self):
        pe = self._build_fake_patient_exam()
        legacy_guidance = {
            "requirements_by_set": {"1001": [{"id": 2001, "name": "legacy"}]},
            "requirement_status": {"2001": False},
            "requirement_set_status": {"1001": False},
            "requirement_defaults": {},
            "classification_choices": {},
            "suggested_actions": {},
            "candidate_requirement_set_ids": [1001],
            "candidate_requirement_set_confidence": 1.0,
            "selected_requirement_set_ids": [],
            "history_context": {},
            "advisory_only": True,
        }
        dtypes_guidance = {
            **legacy_guidance,
            "requirement_status": {"2001": True},
            "requirement_set_status": {"1001": True},
        }
        warning_messages: list[str] = []

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "hybrid_compare",
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_requirement_guidance",
            lambda **kwargs: dtypes_guidance,
        )
        monkeypatch.setattr(
            lookup_service,
            "_evaluate_patient_exam_requirement_guidance_legacy",
            lambda *args, **kwargs: legacy_guidance,
        )
        monkeypatch.setattr(
            lookup_service.logger,
            "warning",
            lambda msg, *args, **kwargs: warning_messages.append(
                msg % args if args else msg
            ),
        )
        try:
            result = lookup_service.evaluate_patient_exam_requirement_guidance(pe)
        finally:
            monkeypatch.undo()

        assert result == dtypes_guidance
        assert any("hybrid_compare divergence" in msg for msg in warning_messages)

    def test_evaluate_guidance_hybrid_compare_falls_back_to_legacy_when_dtypes_missing(
        self,
    ):
        pe = self._build_fake_patient_exam()
        legacy_guidance = {
            "requirements_by_set": {},
            "requirement_status": {},
            "requirement_set_status": {},
            "requirement_defaults": {},
            "classification_choices": {},
            "suggested_actions": {},
            "candidate_requirement_set_ids": [],
            "candidate_requirement_set_confidence": 0.0,
            "selected_requirement_set_ids": [],
            "history_context": {},
            "advisory_only": True,
        }
        warning_messages: list[str] = []

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "hybrid_compare",
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_requirement_guidance",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            lookup_service,
            "_evaluate_patient_exam_requirement_guidance_legacy",
            lambda *args, **kwargs: legacy_guidance,
        )
        monkeypatch.setattr(
            lookup_service.logger,
            "warning",
            lambda msg, *args, **kwargs: warning_messages.append(
                msg % args if args else msg
            ),
        )
        try:
            result = lookup_service.evaluate_patient_exam_requirement_guidance(pe)
        finally:
            monkeypatch.undo()

        assert result == legacy_guidance
        assert any("dtypes guidance unavailable" in msg for msg in warning_messages)

    def test_recompute_hybrid_compare_logs_divergence_and_returns_dtypes(self):
        pe = self._build_fake_patient_exam()

        class _FakeStore:
            def __init__(self):
                self.data = {
                    "patient_examination_id": 123,
                    "selected_requirement_set_ids": [],
                    "selected_choices": {},
                }
                self.recompute_done = False

            def get_all(self):
                return dict(self.data)

            def set(self, key, value):
                self.data[key] = value

            def validate_and_recover_data(self, token):
                assert token == "tok"
                return dict(self.data)

            def get_many(self, keys):
                return {key: self.data.get(key) for key in keys}

            def set_many(self, updates):
                self.data.update(updates)

            def mark_recompute_done(self):
                self.recompute_done = True

        fake_store = _FakeStore()
        legacy_updates = {
            "requirements_by_set": {"1001": [{"id": 2001, "name": "legacy"}]},
            "requirement_status": {"2001": False},
            "requirement_set_status": {"1001": False},
            "requirement_defaults": {},
            "classification_choices": {},
            "suggested_actions": {},
            "candidate_requirement_set_ids": [1001],
            "candidate_requirement_set_confidence": 1.0,
        }
        dtypes_updates = {
            **legacy_updates,
            "requirements_by_set": {"1001": [{"id": 2001, "name": "dtypes"}]},
            "requirement_status": {"2001": True},
            "requirement_set_status": {"1001": True},
        }
        warning_messages: list[str] = []

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "LookupStore",
            lambda *args, **kwargs: fake_store,
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "hybrid_compare",
        )
        monkeypatch.setattr(
            lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: pe,
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            lookup_service,
            "get_patient_examination_history_context",
            lambda *args, **kwargs: {},
        )
        monkeypatch.setattr(
            lookup_service,
            "propose_candidate_requirement_sets",
            lambda **kwargs: SimpleNamespace(
                candidate_requirement_set_ids=[],
                confidence=0.0,
            ),
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_lookup_updates",
            lambda **kwargs: dtypes_updates,
        )
        monkeypatch.setattr(
            lookup_service,
            "_build_legacy_lookup_updates",
            lambda **kwargs: legacy_updates,
        )
        monkeypatch.setattr(
            lookup_service.logger,
            "warning",
            lambda msg, *args, **kwargs: warning_messages.append(
                msg % args if args else msg
            ),
        )
        try:
            result = lookup_service.recompute_lookup("tok")
        finally:
            monkeypatch.undo()

        assert result == dtypes_updates
        assert fake_store.recompute_done is True
        assert any("hybrid_compare divergence" in msg for msg in warning_messages)

    def test_recompute_hybrid_compare_falls_back_to_legacy_when_dtypes_missing(self):
        pe = self._build_fake_patient_exam()

        class _FakeStore:
            def __init__(self):
                self.data = {
                    "patient_examination_id": 123,
                    "selected_requirement_set_ids": [],
                    "selected_choices": {},
                }
                self.recompute_done = False

            def get_all(self):
                return dict(self.data)

            def set(self, key, value):
                self.data[key] = value

            def validate_and_recover_data(self, token):
                assert token == "tok"
                return dict(self.data)

            def get_many(self, keys):
                return {key: self.data.get(key) for key in keys}

            def set_many(self, updates):
                self.data.update(updates)

            def mark_recompute_done(self):
                self.recompute_done = True

        fake_store = _FakeStore()
        legacy_updates = {
            "requirements_by_set": {"1001": [{"id": 2001, "name": "legacy"}]},
            "requirement_status": {"2001": False},
            "requirement_set_status": {"1001": False},
            "requirement_defaults": {},
            "classification_choices": {},
            "suggested_actions": {},
            "candidate_requirement_set_ids": [1001],
            "candidate_requirement_set_confidence": 1.0,
        }
        warning_messages: list[str] = []

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            lookup_service,
            "LookupStore",
            lambda *args, **kwargs: fake_store,
        )
        monkeypatch.setattr(
            lookup_service,
            "get_lookup_requirement_source",
            lambda: "hybrid_compare",
        )
        monkeypatch.setattr(
            lookup_service,
            "load_patient_exam_for_eval",
            lambda pe_id: pe,
        )
        monkeypatch.setattr(
            lookup_service,
            "requirement_sets_for_patient_exam",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            lookup_service,
            "get_patient_examination_history_context",
            lambda *args, **kwargs: {},
        )
        monkeypatch.setattr(
            lookup_service,
            "propose_candidate_requirement_sets",
            lambda **kwargs: SimpleNamespace(
                candidate_requirement_set_ids=[],
                confidence=0.0,
            ),
        )
        monkeypatch.setattr(
            lookup_service,
            "try_build_dtypes_lookup_updates",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            lookup_service,
            "_build_legacy_lookup_updates",
            lambda **kwargs: legacy_updates,
        )
        monkeypatch.setattr(
            lookup_service.logger,
            "warning",
            lambda msg, *args, **kwargs: warning_messages.append(
                msg % args if args else msg
            ),
        )
        try:
            result = lookup_service.recompute_lookup("tok")
        finally:
            monkeypatch.undo()

        assert result == legacy_updates
        assert fake_store.recompute_done is True
        assert any("dtypes updates unavailable" in msg for msg in warning_messages)
