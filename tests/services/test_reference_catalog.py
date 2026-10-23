from importlib.resources import files
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.management import call_command
from django.db import connections
from lx_dtypes.models.interface.DataLoader import DataLoader
from lx_dtypes.models.contracts.reference_catalog import (
    ReferenceCatalogPayload,
    ReferenceKind,
)
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    ReferenceCatalogSnapshot,
)
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.services.reference_data.catalog import (
    catalog_snapshot,
    import_reference_catalog,
    plan_reference_catalog,
    select_catalog,
)
from endoreg_db.services.reference_data.catalog_export import export_reference_catalog
from endoreg_db.services.reference_data.catalog_models import CATALOG_MODELS

pytestmark = pytest.mark.django_db


def snapshot(module: str = "endoreg_reference") -> ReferenceCatalogSnapshot:
    root = Path(str(files("lx_dtypes").joinpath("data")))
    kb = DataLoader(input_dirs=[root]).load_knowledge_base(module)
    return catalog_snapshot(kb)


def canonical(
    payload: ReferenceCatalogPayload,
) -> dict[tuple[ReferenceKind, str, int | None], JsonObject]:
    return {
        record.key: record.model_dump(mode="json", by_alias=True)
        for record in payload.records
    }


def test_fresh_catalog_complete_roundtrip_and_idempotence():
    source = snapshot()
    assert len(source.payload.records) == 673
    assert plan_reference_catalog(source).can_import
    import_reference_catalog(source)
    assert canonical(export_reference_catalog()) == canonical(source.payload)
    assert import_reference_catalog(source).unchanged
    assert ReferenceCatalogImport.objects.count() == 1


def test_legacy_upgrade_preserves_all_primary_keys_and_relations():
    source = snapshot()
    import_reference_catalog(source)
    ReferenceCatalogImport.objects.all().delete()
    before = {
        kind: list(model._default_manager.values_list("pk", flat=True))
        for kind, model in CATALOG_MODELS.items()
    }
    assert canonical(export_reference_catalog()) == canonical(source.payload)
    assert not plan_reference_catalog(source).can_import
    plan = import_reference_catalog(source, adopt_existing=True)
    assert {change.action for change in plan.changes} == {"adopt"}
    assert before == {
        kind: list(model._default_manager.values_list("pk", flat=True))
        for kind, model in CATALOG_MODELS.items()
    }
    assert import_reference_catalog(source).unchanged


def test_final_failure_rolls_back_complete_catalog():
    with patch.object(
        ReferenceCatalogImport,
        "save",
        side_effect=ValueError("injected receipt failure"),
    ):
        with pytest.raises(ValueError, match="injected receipt failure"):
            import_reference_catalog(snapshot())
    assert all(not model._default_manager.exists() for model in CATALOG_MODELS.values())
    assert not ReferenceCatalogImport.objects.exists()


def test_catalog_row_drift_is_reported_and_never_overwritten():
    source = snapshot()
    import_reference_catalog(source)
    row = CATALOG_MODELS[source.payload.records[0].kind]._default_manager.get(
        name=source.payload.records[0].identity.name
    )
    setattr(row, "description", "changed locally")
    row.save()
    plan = plan_reference_catalog(source)
    assert not plan.can_import
    assert "description" in plan.changes[0].differing_fields
    with pytest.raises(ValueError, match="conflicts"):
        import_reference_catalog(source)
    row.refresh_from_db()
    assert getattr(row, "description") == "changed locally"


def test_bootstrap_uses_registered_catalog_without_legacy_loader() -> None:
    call_command("load_base_db_data", stdout=StringIO())
    assert len(export_reference_catalog().records) == 673
    assert ReferenceCatalogImport.objects.get().module == "endoreg_reference"


def test_selected_catalogue_closes_dependencies_and_full_import_reuses_rows() -> None:
    source = snapshot()
    selected = select_catalog(source, {ReferenceKind.MEDICATION})
    import_reference_catalog(selected)
    assert not CATALOG_MODELS[ReferenceKind.CENTER]._default_manager.exists()
    keys = {
        change.identity.name: change.row_id
        for change in plan_reference_catalog(selected).changes
        if change.kind == ReferenceKind.MEDICATION
    }
    import_reference_catalog(source)
    assert keys == {
        change.identity.name: change.row_id
        for change in plan_reference_catalog(source).changes
        if change.kind == ReferenceKind.MEDICATION
    }
    assert ReferenceCatalogImport.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_catalog_imports_publish_one_receipt() -> None:
    source = select_catalog(snapshot(), {ReferenceKind.UNIT})

    def run(_index: int) -> bool:
        try:
            return import_reference_catalog(source).unchanged
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, range(2)))
    assert sorted(results) == [False, True]
    assert ReferenceCatalogImport.objects.count() == 1


@pytest.mark.parametrize(
    "module, expected", [("endoreg_workforce", 39), ("endoreg_green_endoscopy", 826)]
)
def test_optional_catalog_roundtrip_and_legacy_adoption(
    module: str, expected: int
) -> None:
    source = snapshot(module)
    assert len(source.payload.records) == expected
    import_reference_catalog(source)
    assert canonical(export_reference_catalog()) == canonical(source.payload)
    before = {
        (kind, row.pk)
        for kind, model in CATALOG_MODELS.items()
        for row in model._base_manager.all()
    }
    ReferenceCatalogImport.objects.all().delete()
    assert not plan_reference_catalog(source).can_import
    import_reference_catalog(source, adopt_existing=True)
    assert before == {
        (kind, row.pk)
        for kind, model in CATALOG_MODELS.items()
        for row in model._base_manager.all()
    }
    assert import_reference_catalog(source).unchanged


def test_all_optional_compatibility_commands_can_bootstrap_together() -> None:
    for command in (
        "load_profession_data",
        "load_qualification_data",
        "load_shift_data",
        "load_green_endoscopy_wuerzburg_data",
        "load_base_db_data",
    ):
        call_command(command, stdout=StringIO())
    assert len(export_reference_catalog().records) == 865
    for command in (
        "load_profession_data",
        "load_qualification_data",
        "load_shift_data",
        "load_green_endoscopy_wuerzburg_data",
        "load_base_db_data",
    ):
        call_command(command, stdout=StringIO())
    assert len(export_reference_catalog().records) == 865


def test_inactive_workforce_reference_is_not_hidden_during_reconciliation() -> None:
    source = snapshot("endoreg_workforce")
    import_reference_catalog(source)
    model = CATALOG_MODELS[ReferenceKind.QUALIFICATION]
    row = model._base_manager.first()
    assert row is not None
    setattr(row, "is_active", False)
    row.save()
    plan = plan_reference_catalog(source)
    assert not plan.can_import
    assert any("is_active" in change.differing_fields for change in plan.changes)
    with pytest.raises(ValueError, match="conflicts"):
        import_reference_catalog(source)
    assert model._base_manager.filter(pk=row.pk).count() == 1


def test_legacy_nan_weight_exports_as_absent_but_infinity_is_rejected() -> None:
    from endoreg_db.services.reference_data.catalog_export import export_reference_row
    from pydantic import ValidationError

    import_reference_catalog(snapshot("endoreg_green_endoscopy"))
    row = CATALOG_MODELS[ReferenceKind.PRODUCT_WEIGHT]._base_manager.first()
    assert row is not None
    setattr(row, "measured", float("nan"))
    assert (
        export_reference_row(ReferenceKind.PRODUCT_WEIGHT, row).model_dump()["measured"]
        is None
    )
    setattr(row, "measured", float("inf"))
    with pytest.raises(ValidationError):
        export_reference_row(ReferenceKind.PRODUCT_WEIGHT, row)


def test_explicit_selection_can_name_every_supported_reference_kind() -> None:
    selected = select_catalog(snapshot("endoreg_green_endoscopy"), set(ReferenceKind))
    assert len(selected.projection) > 1024
    assert len(selected.payload.records) == 826
    import_reference_catalog(selected)
    assert ReferenceCatalogImport.objects.get().projection == selected.projection
