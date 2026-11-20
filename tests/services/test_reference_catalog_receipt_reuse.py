"""Unchanged catalogues are recognised by receipt digest instead of a full row comparison."""

from importlib.resources import files
from io import StringIO
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from lx_dtypes.models.contracts.reference_catalog import (
    ReferenceCatalogPayload,
    ReferenceKind,
)
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    ReferenceCatalogSnapshot,
)
from lx_dtypes.models.interface.DataLoader import DataLoader

from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.services.reference_data.catalog import (
    CatalogPlan,
    catalog_snapshot,
    import_reference_catalog,
    plan_reference_catalog,
    reference_catalog_transaction,
    reuse_current_catalog,
    select_catalog,
)
from endoreg_db.services.reference_data.catalog_models import CATALOG_MODELS

pytestmark = pytest.mark.django_db

_IMPORT_COMMAND = "endoreg_db.management.commands.import_reference_catalog"
_MIGRATION = "endoreg_db.services.reference_data.catalog_migration"


def _unit_snapshot() -> ReferenceCatalogSnapshot:
    root = Path(str(files("lx_dtypes").joinpath("data")))
    kb = DataLoader(input_dirs=[root]).load_knowledge_base("endoreg_reference")
    return select_catalog(catalog_snapshot(kb), {ReferenceKind.UNIT})


def _with_changed_description(
    snapshot: ReferenceCatalogSnapshot,
) -> ReferenceCatalogSnapshot:
    records = [
        record.model_dump(mode="json", by_alias=True)
        for record in snapshot.payload.records
    ]
    records[0]["description"] = "changed by study setup"
    payload = ReferenceCatalogPayload.model_validate({"records": records})
    return ReferenceCatalogSnapshot(
        identity=snapshot.identity,
        payload=payload,
        projection=snapshot.projection,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(
            snapshot.identity, payload, snapshot.projection
        ),
    )


def _reuse(snapshot: ReferenceCatalogSnapshot) -> CatalogPlan | None:
    with reference_catalog_transaction():
        return reuse_current_catalog(snapshot)


def _full_comparison_forbidden(*_args: object, **_kwargs: object) -> NoReturn:
    raise AssertionError("Unchanged catalogue must not run the full row comparison")


def test_identical_receipt_reuses_bound_rows() -> None:
    source = _unit_snapshot()
    import_reference_catalog(source)

    reused = _reuse(source)

    assert reused is not None
    assert reused.unchanged and reused.can_import
    assert reused.snapshot_id == source.snapshot_id
    full = plan_reference_catalog(source)
    assert [(c.kind, c.identity, c.action, c.row_id) for c in reused.changes] == [
        (c.kind, c.identity, c.action, c.row_id) for c in full.changes
    ]


@pytest.mark.parametrize("state", ["no_receipt", "deleted_row", "changed_content"])
def test_changed_state_falls_back_to_full_reconciliation(state: str) -> None:
    source = _unit_snapshot()
    candidate = source
    if state != "no_receipt":
        import_reference_catalog(source)
    if state == "deleted_row":
        unit = source.payload.records[0]
        CATALOG_MODELS[ReferenceKind.UNIT]._base_manager.filter(
            name=unit.identity.name
        ).delete()
    if state == "changed_content":
        candidate = _with_changed_description(source)

    assert _reuse(candidate) is None
    if state != "no_receipt":
        # The full path still fails closed for drift and immutable versions.
        assert not plan_reference_catalog(candidate).can_import


@pytest.mark.django_db(transaction=True)
def test_reuse_requires_reference_transaction() -> None:
    with pytest.raises(RuntimeError, match="reference transaction"):
        reuse_current_catalog(_unit_snapshot())


def test_repeated_command_skips_full_comparison() -> None:
    call_command("load_unit_data", stdout=StringIO())
    receipts = ReferenceCatalogImport.objects.count()

    with patch(f"{_IMPORT_COMMAND}.plan_reference_catalog", _full_comparison_forbidden):
        output = StringIO()
        call_command("load_unit_data", stdout=output)

    assert '"unchanged": true' in output.getvalue()
    assert ReferenceCatalogImport.objects.count() == receipts


def test_verify_detects_field_drift_that_reuse_tolerates() -> None:
    call_command("load_unit_data", stdout=StringIO())
    unit = _unit_snapshot().payload.records[0]
    row = CATALOG_MODELS[ReferenceKind.UNIT]._base_manager.get(name=unit.identity.name)
    setattr(row, "description", "changed locally")
    row.save()

    call_command("load_unit_data", stdout=StringIO())
    with pytest.raises(CommandError, match="conflicts"):
        call_command("load_unit_data", verify=True, stdout=StringIO())
    row.refresh_from_db()
    assert getattr(row, "description") == "changed locally"


def test_repeated_reconcile_startup_skips_full_comparison() -> None:
    call_command("load_base_db_data", reconcile_legacy=True, stdout=StringIO())

    with (
        patch(f"{_MIGRATION}.plan_catalog_migration", _full_comparison_forbidden),
        patch(f"{_MIGRATION}.plan_reference_catalog", _full_comparison_forbidden),
    ):
        output = StringIO()
        call_command("load_base_db_data", reconcile_legacy=True, stdout=output)

    assert '"unchanged": true' in output.getvalue()
    assert ReferenceCatalogImport.objects.count() == 1
