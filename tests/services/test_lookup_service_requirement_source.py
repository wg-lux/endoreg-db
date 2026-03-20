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
