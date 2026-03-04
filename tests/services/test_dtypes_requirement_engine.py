from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from endoreg_db.services import dtypes_requirement_service as drs


class _QueryConditionRule(BaseModel):
    classification: str
    comparator: str = "eq"
    value: object | None = None


class _QueryRequiredClassification(BaseModel):
    classification: str


class _QueryCondition(BaseModel):
    any: list[_QueryConditionRule] = Field(default_factory=list)
    all: list[_QueryConditionRule] = Field(default_factory=list)
    then_requires: list[_QueryRequiredClassification] = Field(default_factory=list)


class _QueryModel(BaseModel):
    finding: str | None = None
    operator: str | None = None
    condition: _QueryCondition | None = None


class _RelatedManager:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


def _make_patient_finding_classification(
    *,
    classification: str,
    choice: str,
    numerical_descriptors=None,
    subcategories=None,
):
    return SimpleNamespace(
        is_active=True,
        classification=SimpleNamespace(name=classification, id=None),
        classification_choice=SimpleNamespace(name=choice, id=None),
        numerical_descriptors=numerical_descriptors or {},
        subcategories=subcategories or {},
    )


def _make_patient_finding(*, finding: str, classifications=None):
    return SimpleNamespace(
        is_active=True,
        finding=SimpleNamespace(name=finding, id=None),
        classifications=_RelatedManager(classifications or []),
    )


def _make_patient_exam(*, examination: str, patient_findings=None):
    return SimpleNamespace(
        id=123,
        examination=SimpleNamespace(
            name=examination,
            get_available_findings=lambda: [],
        ),
        patient_findings=_RelatedManager(patient_findings or []),
    )


def _make_findings_validator(*, name: str, finding: str, operator: str, query: dict):
    return SimpleNamespace(
        name=name,
        finding=finding,
        operator=operator,
        query=query,
    )


def _make_examination_validator(
    *,
    name: str,
    finding_validators: list[str],
    examination_validators: list[str],
):
    return SimpleNamespace(
        name=name,
        finding_validators=finding_validators,
        examination_validators=examination_validators,
    )


def _make_template(
    *,
    name: str,
    examination: str,
    findings_validators: list[str],
    examination_validators: list[str],
):
    return SimpleNamespace(
        name=name,
        examination=examination,
        report_sections=[],
        validators=SimpleNamespace(
            findings_validators=findings_validators,
            examination_validators=examination_validators,
        ),
    )


def _patch_kb(monkeypatch: pytest.MonkeyPatch, kb):
    monkeypatch.setattr(drs, "_load_dtypes_kb", lambda module_name: kb)
    monkeypatch.setattr(drs, "get_lookup_dtypes_module_name", lambda: "test_module")
    monkeypatch.setattr(drs, "_fill_missing_finding_ids", lambda names, mapping: None)
    monkeypatch.setattr(
        drs, "_fill_missing_classification_ids", lambda names, mapping: None
    )


def test_guidance_returns_none_when_no_template_matches_examination():
    kb = SimpleNamespace(
        report_template={
            "other_template": _make_template(
                name="other_template",
                examination="gastroscopy",
                findings_validators=[],
                examination_validators=[],
            )
        }
    )
    pe = _make_patient_exam(examination="colonoscopy")
    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        result = drs.try_build_dtypes_requirement_guidance(pe=pe)
    finally:
        monkeypatch.undo()

    assert result is None


def test_guidance_evaluates_exists_validator_and_suggests_add_finding():
    kb = SimpleNamespace(
        report_template={
            "template_a": _make_template(
                name="template_a",
                examination="colonoscopy",
                findings_validators=["fv_missing"],
                examination_validators=[],
            )
        },
        findings_validator={
            "fv_missing": _make_findings_validator(
                name="fv_missing",
                finding="colon_polyp",
                operator="exists",
                query={"finding": "colon_polyp", "operator": "exists"},
            )
        },
        examination_validator={},
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(examination="colonoscopy")
    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        result = drs.try_build_dtypes_requirement_guidance(pe=pe)
    finally:
        monkeypatch.undo()

    assert result is not None
    assert result["advisory_only"] is True
    set_id = result["candidate_requirement_set_ids"][0]
    requirement_entry = result["requirements_by_set"][str(set_id)][0]
    req_key = str(requirement_entry["id"])
    assert result["requirement_status"][req_key] is False
    assert result["requirement_set_status"][str(set_id)] is False
    action = result["suggested_actions"][req_key][0]
    assert action["type"] == "add_finding"
    assert action["finding_name"] == "colon_polyp"


def test_guidance_evaluates_conditional_then_requires_when_missing():
    kb = SimpleNamespace(
        report_template={
            "template_conditional": _make_template(
                name="template_conditional",
                examination="gastroscopy",
                findings_validators=["fv_large_requires_lst"],
                examination_validators=[],
            )
        },
        findings_validator={
            "fv_large_requires_lst": _make_findings_validator(
                name="fv_large_requires_lst",
                finding="esophagus_polyp",
                operator="condition",
                query={
                    "finding": "esophagus_polyp",
                    "operator": "condition",
                    "condition": {
                        "any": [
                            {
                                "classification": "size_mm",
                                "comparator": "gt",
                                "value": 10,
                            }
                        ],
                        "then_requires": [{"classification": "lst"}],
                    },
                },
            )
        },
        examination_validator={},
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(
        examination="gastroscopy",
        patient_findings=[
            _make_patient_finding(
                finding="esophagus_polyp",
                classifications=[
                    _make_patient_finding_classification(
                        classification="size_mm",
                        choice="12",
                    )
                ],
            )
        ],
    )
    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        result = drs.try_build_dtypes_requirement_guidance(pe=pe)
    finally:
        monkeypatch.undo()

    assert result is not None
    set_id = result["candidate_requirement_set_ids"][0]
    requirement_entry = result["requirements_by_set"][str(set_id)][0]
    req_key = str(requirement_entry["id"])
    assert result["requirement_status"][req_key] is False
    action = result["suggested_actions"][req_key][0]
    assert action["type"] == "add_finding"
    assert "lst" in action.get("classification_names", [])


def test_guidance_evaluates_conditional_then_requires_when_present():
    kb = SimpleNamespace(
        report_template={
            "template_conditional": _make_template(
                name="template_conditional",
                examination="gastroscopy",
                findings_validators=["fv_large_requires_lst"],
                examination_validators=[],
            )
        },
        findings_validator={
            "fv_large_requires_lst": _make_findings_validator(
                name="fv_large_requires_lst",
                finding="esophagus_polyp",
                operator="condition",
                query={
                    "finding": "esophagus_polyp",
                    "operator": "condition",
                    "condition": {
                        "any": [
                            {
                                "classification": "size_mm",
                                "comparator": "gt",
                                "value": 10,
                            }
                        ],
                        "then_requires": [{"classification": "lst"}],
                    },
                },
            )
        },
        examination_validator={},
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(
        examination="gastroscopy",
        patient_findings=[
            _make_patient_finding(
                finding="esophagus_polyp",
                classifications=[
                    _make_patient_finding_classification(
                        classification="size_mm",
                        choice="12",
                    ),
                    _make_patient_finding_classification(
                        classification="lst",
                        choice="present",
                    ),
                ],
            )
        ],
    )
    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        result = drs.try_build_dtypes_requirement_guidance(pe=pe)
    finally:
        monkeypatch.undo()

    assert result is not None
    set_id = result["candidate_requirement_set_ids"][0]
    requirement_entry = result["requirements_by_set"][str(set_id)][0]
    req_key = str(requirement_entry["id"])
    assert result["requirement_status"][req_key] is True
    assert req_key not in result["suggested_actions"]
    assert result["requirement_set_status"][str(set_id)] is True


def test_guidance_evaluates_nested_examination_validators():
    kb = SimpleNamespace(
        report_template={
            "template_nested": _make_template(
                name="template_nested",
                examination="colonoscopy",
                findings_validators=[],
                examination_validators=["ev_parent"],
            )
        },
        findings_validator={
            "fv_missing": _make_findings_validator(
                name="fv_missing",
                finding="colon_minimum_doc",
                operator="exists",
                query={"finding": "colon_minimum_doc", "operator": "exists"},
            )
        },
        examination_validator={
            "ev_parent": _make_examination_validator(
                name="ev_parent",
                finding_validators=[],
                examination_validators=["ev_child"],
            ),
            "ev_child": _make_examination_validator(
                name="ev_child",
                finding_validators=["fv_missing"],
                examination_validators=[],
            ),
        },
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(examination="colonoscopy")
    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        result = drs.try_build_dtypes_requirement_guidance(pe=pe)
    finally:
        monkeypatch.undo()

    assert result is not None
    set_id = result["candidate_requirement_set_ids"][0]
    requirement_entries = result["requirements_by_set"][str(set_id)]
    assert len(requirement_entries) == 3
    by_name = {entry["name"]: str(entry["id"]) for entry in requirement_entries}

    assert (
        result["requirement_status"][by_name["findings_validator:fv_missing"]] is False
    )
    assert (
        result["requirement_status"][by_name["examination_validator:ev_parent"]]
        is False
    )
    assert (
        result["requirement_status"][by_name["examination_validator:ev_child"]] is False
    )
    assert result["requirement_set_status"][str(set_id)] is False


def test_lookup_updates_include_only_lookup_derived_keys():
    kb = SimpleNamespace(
        report_template={
            "template_updates": _make_template(
                name="template_updates",
                examination="colonoscopy",
                findings_validators=["fv_missing"],
                examination_validators=[],
            )
        },
        findings_validator={
            "fv_missing": _make_findings_validator(
                name="fv_missing",
                finding="colon_polyp",
                operator="exists",
                query={"finding": "colon_polyp", "operator": "exists"},
            )
        },
        examination_validator={},
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(examination="colonoscopy")
    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        updates = drs.try_build_dtypes_lookup_updates(
            pe=pe,
            selected_requirement_set_ids=[],
        )
    finally:
        monkeypatch.undo()

    assert updates is not None
    assert set(updates.keys()) == {
        "requirements_by_set",
        "requirement_status",
        "requirement_set_status",
        "requirement_defaults",
        "classification_choices",
        "suggested_actions",
        "candidate_requirement_set_ids",
        "candidate_requirement_set_confidence",
    }


def test_guidance_selected_requirement_set_ids_filters_to_known_template_ids():
    kb = SimpleNamespace(
        report_template={
            "template_a": _make_template(
                name="template_a",
                examination="colonoscopy",
                findings_validators=["fv_a"],
                examination_validators=[],
            ),
            "template_b": _make_template(
                name="template_b",
                examination="colonoscopy",
                findings_validators=["fv_b"],
                examination_validators=[],
            ),
        },
        findings_validator={
            "fv_a": _make_findings_validator(
                name="fv_a",
                finding="finding_a",
                operator="exists",
                query={"finding": "finding_a", "operator": "exists"},
            ),
            "fv_b": _make_findings_validator(
                name="fv_b",
                finding="finding_b",
                operator="exists",
                query={"finding": "finding_b", "operator": "exists"},
            ),
        },
        examination_validator={},
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(
        examination="colonoscopy",
        patient_findings=[_make_patient_finding(finding="finding_a")],
    )

    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        unfiltered = drs.try_build_dtypes_requirement_guidance(pe=pe)
        assert unfiltered is not None
        template_ids = unfiltered["candidate_requirement_set_ids"]
        assert len(template_ids) == 2

        filtered = drs.try_build_dtypes_requirement_guidance(
            pe=pe,
            selected_requirement_set_ids=[template_ids[0], 999999],
        )
    finally:
        monkeypatch.undo()

    assert filtered is not None
    assert sorted(filtered["candidate_requirement_set_ids"]) == sorted(template_ids)
    assert list(filtered["requirements_by_set"].keys()) == [str(template_ids[0])]
    assert list(filtered["requirement_set_status"].keys()) == [str(template_ids[0])]


def test_guidance_returns_none_when_pe_data_extract_error():
    kb = SimpleNamespace(
        report_template={
            "template_a": _make_template(
                name="template_a",
                examination="colonoscopy",
                findings_validators=["fv_a"],
                examination_validators=[],
            )
        },
        findings_validator={
            "fv_a": _make_findings_validator(
                name="fv_a",
                finding="finding_a",
                operator="exists",
                query={"finding": "finding_a", "operator": "exists"},
            )
        },
        examination_validator={},
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(examination="colonoscopy")

    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    monkeypatch.setattr(
        drs,
        "_collect_patient_findings",
        lambda pe: (_ for _ in ()).throw(
            drs.DtypesRequirementEvaluationError("forced-extract-failure")
        ),
    )
    try:
        result = drs.try_build_dtypes_requirement_guidance(pe=pe)
    finally:
        monkeypatch.undo()

    assert result is None


def test_assign_stable_lookup_ids_is_order_independent_even_on_collisions():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        drs,
        "_stable_lookup_id_for_name",
        lambda name: 1 if "#" not in name else 2,
    )
    try:
        ids_a = drs._assign_stable_lookup_ids(["template:b", "template:a"])
        ids_b = drs._assign_stable_lookup_ids(["template:a", "template:b"])
    finally:
        monkeypatch.undo()

    assert ids_a == ids_b
    assert set(ids_a.values()) == {1, 2}


def test_guidance_evaluates_pydantic_findings_validator_query_model():
    query_model = _QueryModel(
        finding="esophagus_polyp",
        operator="condition",
        condition=_QueryCondition(
            any=[
                _QueryConditionRule(
                    classification="size_mm",
                    comparator="gt",
                    value=10,
                )
            ],
            then_requires=[_QueryRequiredClassification(classification="lst")],
        ),
    )

    kb = SimpleNamespace(
        report_template={
            "template_conditional": _make_template(
                name="template_conditional",
                examination="gastroscopy",
                findings_validators=["fv_large_requires_lst"],
                examination_validators=[],
            )
        },
        findings_validator={
            "fv_large_requires_lst": _make_findings_validator(
                name="fv_large_requires_lst",
                finding="esophagus_polyp",
                operator="condition",
                query=query_model,
            )
        },
        examination_validator={},
        report_template_section={},
        report_finding={},
    )
    pe = _make_patient_exam(
        examination="gastroscopy",
        patient_findings=[
            _make_patient_finding(
                finding="esophagus_polyp",
                classifications=[
                    _make_patient_finding_classification(
                        classification="size_mm",
                        choice="12",
                    )
                ],
            )
        ],
    )

    monkeypatch = pytest.MonkeyPatch()
    _patch_kb(monkeypatch, kb)
    try:
        result = drs.try_build_dtypes_requirement_guidance(pe=pe)
    finally:
        monkeypatch.undo()

    assert result is not None
    set_id = result["candidate_requirement_set_ids"][0]
    requirement_entry = result["requirements_by_set"][str(set_id)][0]
    req_key = str(requirement_entry["id"])
    assert result["requirement_status"][req_key] is False
    action = result["suggested_actions"][req_key][0]
    assert action["type"] == "add_finding"
    assert "lst" in action.get("classification_names", [])
