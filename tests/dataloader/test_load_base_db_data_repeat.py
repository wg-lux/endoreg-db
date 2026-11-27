from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from endoreg_db.services.reference_data.catalog import CatalogPlan
from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from tests.helpers.data_loader import load_data


def _run(*args: str) -> CatalogPlan:
    out = StringIO()
    call_command("load_base_db_data", *args, stdout=out)
    return CatalogPlan.model_validate_json(out.getvalue())


@pytest.mark.django_db
def test_repeated_base_data_load_is_idempotent(packaged_registry: Path) -> None:
    del packaged_registry
    first = _run()
    assert first.can_import
    assert first.changes

    second = _run()
    assert second.unchanged
    assert second.can_import
    assert second.snapshot_id == first.snapshot_id
    assert {change.action for change in second.changes} <= {"reuse"}

    assert first.clinical_plan is not None
    assert second.clinical_plan is not None
    assert second.clinical_plan.unchanged


@pytest.mark.django_db
def test_compatibility_loaders_use_current_sources_together() -> None:
    _run()
    load_data()
    first = _run()
    assert first.unchanged
    load_data()
    assert _run().unchanged
    assert not ReferenceCatalogImport.objects.filter(
        module="endoreg_reference", version="1.0.0"
    ).exists()
