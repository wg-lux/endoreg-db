from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.check_lx_dtypes_model_inventory import (
    ModelInventory,
    ModelInventoryEntry,
    ModelKind,
    ModelTarget,
    classification_errors,
    compare_inventory,
    declares_abstract_meta,
    discover_boundary_references,
    load_inventory,
    refresh_runtime_metadata,
    timebox_unclassified_models,
)


def _entry(
    label: str,
    *,
    db_table: str | None = None,
) -> ModelInventoryEntry:
    return ModelInventoryEntry(
        label=label,
        kind=ModelKind.REGISTERED,
        python_path=f"endoreg_db.models.example.{label}",
        source_path="endoreg_db/models/example.py",
        base_classes=["django.db.models.base.Model"],
        db_table=db_table or label.lower(),
        abstract=False,
        proxy=False,
        managed=True,
        json_fields=[],
        custom_clean=False,
        custom_save=False,
        target=ModelTarget.UNCLASSIFIED,
        owner="endoreg_db maintainers",
        rationale="Test fixture awaiting classification.",
    )


def _inventory(*entries: ModelInventoryEntry) -> ModelInventory:
    return ModelInventory(
        feature_id="lx_dtypes_model_standardization",
        models=list(entries),
    )


def test_compare_inventory_accepts_matching_runtime_shape() -> None:
    entry = _entry("endoreg_db.Example")

    comparison = compare_inventory((entry,), _inventory(entry))

    assert comparison.is_clean


def test_compare_inventory_reports_new_stale_and_changed_models() -> None:
    expected_changed = _entry("endoreg_db.Changed")
    actual_changed = _entry("endoreg_db.Changed", db_table="renamed_table")
    new = _entry("endoreg_db.New")
    stale = _entry("endoreg_db.Stale")

    comparison = compare_inventory(
        (actual_changed, new),
        _inventory(expected_changed, stale),
    )

    assert comparison.new_models == (new,)
    assert comparison.stale_models == (stale,)
    assert comparison.changed_models == ((expected_changed, actual_changed),)
    assert not comparison.is_clean


def test_refresh_runtime_metadata_preserves_reviewed_classification(
    tmp_path: Path,
) -> None:
    expected = _entry("endoreg_db.Example")
    expected_data = expected.model_dump()
    expected_data.update(
        {
            "target": ModelTarget.PERSISTENCE_ONLY,
            "owner": "review owner",
            "rationale": "Reviewed persistence-only model.",
            "ownership_evidence": ["review:evidence"],
        }
    )
    reviewed = ModelInventoryEntry.model_validate(expected_data)
    actual_data = reviewed.model_dump()
    actual_data.update(
        {
            "db_table": "renamed_table",
            "custom_clean": True,
            "module_endoreg_schema_imports": ["endoreg_db.schemas.example"],
        }
    )
    actual = ModelInventoryEntry.model_validate(actual_data)
    path = tmp_path / "inventory.yml"

    refresh_runtime_metadata(
        path=path,
        inventory=_inventory(reviewed),
        discovered=(actual,),
    )

    refreshed = load_inventory(path).models[0]
    assert refreshed.db_table == "renamed_table"
    assert refreshed.custom_clean is True
    assert refreshed.module_endoreg_schema_imports == ["endoreg_db.schemas.example"]
    assert refreshed.target is ModelTarget.PERSISTENCE_ONLY
    assert refreshed.owner == "review owner"
    assert refreshed.rationale == "Reviewed persistence-only model."
    assert refreshed.ownership_evidence == ["review:evidence"]


def test_classified_model_requires_ownership_evidence() -> None:
    entry_data = _entry("endoreg_db.Example").model_dump()
    entry_data["target"] = ModelTarget.PERSISTENCE_ONLY

    with pytest.raises(ValidationError, match="requires ownership_evidence"):
        ModelInventoryEntry.model_validate(entry_data)


def test_temporary_exception_requires_time_bounded_exit_metadata() -> None:
    entry_data = _entry("endoreg_db.Example").model_dump()
    entry_data.update(
        {
            "target": ModelTarget.TEMPORARY_EXCEPTION,
            "ownership_evidence": ["review:evidence"],
        }
    )

    with pytest.raises(ValidationError, match="requires owner, review date"):
        ModelInventoryEntry.model_validate(entry_data)


def test_timebox_unclassified_models_records_explicit_review_contract(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.yml"

    timebox_unclassified_models(
        path=path,
        inventory=_inventory(_entry("endoreg_db.Example")),
        review_by=date(2026, 10, 31),
    )

    timeboxed = load_inventory(path).models[0]
    assert timeboxed.target is ModelTarget.TEMPORARY_EXCEPTION
    assert timeboxed.exception_review_by == date(2026, 10, 31)
    assert timeboxed.exception_owner == "endoreg_db and lx_dtypes maintainers"
    assert timeboxed.exception_exit_criteria is not None
    assert timeboxed.ownership_evidence


def test_classification_guard_rejects_unclassified_and_expired_exceptions() -> None:
    unclassified = _entry("endoreg_db.Unclassified")
    expired_data = _entry("endoreg_db.Expired").model_dump()
    expired_data.update(
        {
            "target": ModelTarget.TEMPORARY_EXCEPTION,
            "ownership_evidence": ["review:evidence"],
            "exception_owner": "test owner",
            "exception_review_by": date(2026, 7, 1),
            "exception_exit_criteria": "Complete the domain review.",
        }
    )
    expired = ModelInventoryEntry.model_validate(expired_data)

    errors = classification_errors(
        _inventory(expired, unclassified),
        today=date(2026, 7, 30),
    )

    assert errors == (
        "endoreg_db.Expired temporary exception expired on 2026-07-01",
        "endoreg_db.Unclassified remains unclassified",
    )


def test_boundary_reference_discovery_tracks_static_and_dynamic_consumers(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "endoreg_db"
    service_path = source_root / "services" / "example.py"
    service_path.parent.mkdir(parents=True)
    service_path.write_text(
        "from somewhere import Example\n"
        "MODEL_LABEL = 'endoreg_db.Example'\n"
        "def use(value: Example) -> Example:\n"
        "    return value\n",
        encoding="utf-8",
    )
    model_path = source_root / "models" / "example.py"
    model_path.parent.mkdir(parents=True)
    model_path.write_text("Example = object\n", encoding="utf-8")
    entry = _entry("endoreg_db.Example")

    references = discover_boundary_references((entry,), source_root=source_root)

    assert references == {
        "endoreg_db.Example": (
            ("service:endoreg_db/services/example.py",),
            ("endoreg_db/services/example.py",),
        )
    }


def test_abstract_meta_detection_requires_literal_true() -> None:
    tree = ast.parse(
        """
class AbstractExample:
    class Meta:
        abstract = True

class ConcreteExample:
    class Meta:
        abstract = False
"""
    )
    abstract_example, concrete_example = (
        node for node in tree.body if isinstance(node, ast.ClassDef)
    )

    assert declares_abstract_meta(abstract_example)
    assert not declares_abstract_meta(concrete_example)
