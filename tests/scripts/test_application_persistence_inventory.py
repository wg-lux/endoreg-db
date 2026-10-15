"""Freeze reviewed ownership metadata for the transferred persistence cohort."""

import pytest

from scripts.check_lx_dtypes_model_inventory import (
    DEFAULT_INVENTORY,
    compare_inventory,
    discover_models,
    load_inventory,
)

pytestmark = pytest.mark.no_db


def test_transferred_cohort_matches_reviewed_inventory() -> None:
    names = {
        "AIDatasetSplitPlan",
        "OutboundHubTransferJob",
        "StorageArtifactPublication",
        "StorageNodeActionReceipt",
        "StorageOperatorDispatchReceipt",
        "VideoTranscodeJob",
    }
    labels = {f"endoreg_db.{name}" for name in names}
    inventory = load_inventory(DEFAULT_INVENTORY)
    cohort = inventory.model_copy(
        update={
            "models": [entry for entry in inventory.models if entry.label in labels],
        }
    )
    discovered = tuple(entry for entry in discover_models() if entry.label in labels)
    assert {entry.label for entry in discovered} == labels
    result = compare_inventory(discovered, cohort)
    assert not result.new_models
    assert not result.stale_models
    assert not result.changed_models
    assert all(entry.owner == "endoreg_db maintainers" for entry in cohort.models)
