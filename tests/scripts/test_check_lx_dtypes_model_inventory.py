from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.check_lx_dtypes_model_inventory import (
    DEFAULT_INVENTORY,
    ModelInventory,
    ModelInventoryEntry,
    ModelKind,
    ModelTarget,
    compare_inventory,
    declares_abstract_meta,
    discover_models,
    load_inventory,
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


def test_classified_model_requires_ownership_evidence() -> None:
    entry_data = _entry("endoreg_db.Example").model_dump()
    entry_data["target"] = ModelTarget.PERSISTENCE_ONLY

    with pytest.raises(ValidationError, match="requires ownership_evidence"):
        ModelInventoryEntry.model_validate(entry_data)


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


def test_repository_inventory_matches_discovered_models() -> None:
    inventory = load_inventory(Path(DEFAULT_INVENTORY))
    discovered = discover_models()

    comparison = compare_inventory(discovered, inventory)

    assert comparison.is_clean
    assert len(discovered) == len(inventory.models)
    assert sum(item.kind is ModelKind.ABSTRACT for item in discovered) == 5
    assert all(
        item.target is ModelTarget.PERSISTENCE_ONLY
        and bool(item.ownership_evidence)
        for item in inventory.models
        if item.kind is ModelKind.ABSTRACT
    )
