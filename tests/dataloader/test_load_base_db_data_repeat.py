from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from endoreg_db.services.reference_data.catalog import CatalogPlan


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

    legacy = _run("--reconcile-legacy")
    assert legacy.unchanged
    assert legacy.can_import
    assert legacy.snapshot_id == first.snapshot_id
