from importlib.resources import files
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import json

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
    ReferenceCatalogReceipt,
)
from lx_dtypes.models.contracts.reference_migration import (
    ReferenceMigrationCatalog,
    ReferenceMigrationRule,
    ReferenceMigrationTarget,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity

from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.models.other.information_source import InformationSource
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
    if module == "endoreg_reference":
        from lx_dtypes.knowledge_bases import get_packaged_knowledge_base

        root = get_packaged_knowledge_base(module, "1.0.0").installed_data_root()
    from lx_dtypes.knowledge_bases import get_packaged_knowledge_base

    kb = DataLoader(
        input_dirs=[
            root / "terminology" / module if module != "endoreg_reference" else root,
            get_packaged_knowledge_base(
                "endoreg_reference", "1.0.0"
            ).installed_data_root(),
        ]
    ).load_knowledge_base(module)
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
    receipt = ReferenceCatalogImport.objects.get()
    assert receipt.module == "endoreg_reference"
    assert receipt.version == "2.0.0"
    assert (
        ReferenceCatalogReceipt.model_validate(
            receipt.payload
        ).snapshot.clinical_package
        is not None
    )


def test_bootstrap_records_legacy_source_migration_once(
    packaged_registry: Path,
) -> None:
    row = InformationSource.objects.create(name="unknown", description=None)
    dry_run = StringIO()
    call_command("load_base_db_data", dry_run=True, stdout=dry_run)
    row.refresh_from_db()
    assert row.description is None
    assert not ReferenceCatalogImport.objects.exists()

    output = StringIO()
    call_command("load_base_db_data", stdout=output)
    history = json.loads(output.getvalue())["clinical_plan"]["applied_migrations"]
    assert len(history) == 1
    assert history[0]["changes"][0]["decision"] == "fill_missing"
    row.refresh_from_db()
    assert row.description == "Data source is unknown."

    repeated = StringIO()
    call_command("load_base_db_data", stdout=repeated)
    result = json.loads(repeated.getvalue())
    assert result["unchanged"]
    assert result["clinical_plan"]["applied_migrations"] == history
    assert InformationSource.objects.get(name="unknown").pk == row.pk


def test_catalogue_migration_and_history_commit_together() -> None:
    kind = ReferenceKind.GENDER
    old = _with_record_field(
        select_catalog(snapshot(), {kind}), kind, "unknown", "description", "Old text."
    )
    import_reference_catalog(old)
    changed = _with_record_field(old, kind, "unknown", "description", "Unspecified.")
    identity = KnowledgeBaseIdentity(
        knowledge_base_module=old.identity.knowledge_base_module,
        knowledge_base_version="2.1.0",
    )
    updated = ReferenceCatalogSnapshot(
        identity=identity,
        payload=changed.payload,
        projection=changed.projection,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(
            identity, changed.payload, changed.projection
        ),
    )
    rule = ReferenceMigrationRule(
        kind=kind,
        identity=next(
            record.identity
            for record in old.payload.records
            if record.identity.name == "unknown"
        ),
        field="description",
        existing="Old text.",
        proposed="Unspecified.",
        reason="Reviewed wording correction.",
    )
    rules = ReferenceMigrationCatalog(
        schema_version=1,
        targets=[
            ReferenceMigrationTarget(
                module=identity.knowledge_base_module,
                version=identity.knowledge_base_version,
                rules=[rule],
            )
        ],
    )
    with patch(
        "endoreg_db.services.reference_data.catalog.load_reference_migrations",
        return_value=rules,
    ):
        proposed = plan_reference_catalog(updated)
        assert proposed.can_import
        assert [change.action for change in proposed.changes].count("migrate") == 1
        assert not proposed.applied_migrations
        with patch.object(
            ReferenceCatalogImport, "save", side_effect=ValueError("receipt failed")
        ):
            with pytest.raises(ValueError, match="receipt failed"):
                import_reference_catalog(updated)
        assert plan_reference_catalog(updated) == proposed
        assert ReferenceCatalogImport.objects.count() == 1

        applied = import_reference_catalog(updated)
        replay = import_reference_catalog(updated)
    (migration,) = applied.applied_migrations
    assert migration.updates == {"description": "Unspecified."}
    assert migration.changes[0].reason == rule.reason
    assert replay.unchanged
    assert replay.applied_migrations == [migration]
    assert not plan_reference_catalog(old).can_import


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
    assert len(export_reference_catalog().records) == 541
    for command in (
        "load_profession_data",
        "load_qualification_data",
        "load_shift_data",
        "load_green_endoscopy_wuerzburg_data",
        "load_base_db_data",
    ):
        call_command(command, stdout=StringIO())
    assert len(export_reference_catalog().records) == 541


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


def _with_record_field(
    source: ReferenceCatalogSnapshot,
    kind: ReferenceKind,
    name: str,
    field: str,
    value: object,
) -> ReferenceCatalogSnapshot:
    records = [
        record.model_dump(mode="json", by_alias=True)
        for record in source.payload.records
    ]
    target = next(
        record
        for record in records
        if record["record_type"] == kind.value and record["name"] == name
    )
    target[field] = value
    payload = ReferenceCatalogPayload.model_validate({"records": records})
    return ReferenceCatalogSnapshot(
        identity=source.identity,
        payload=payload,
        projection=source.projection,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(
            source.identity, payload, source.projection
        ),
    )


def test_catalog_entry_name_de_is_imported_and_exported_when_contract_has_it() -> None:
    # Risk is the only entry contract that already carries name_de.
    source = _with_record_field(
        select_catalog(snapshot(), {ReferenceKind.RISK}),
        ReferenceKind.RISK,
        "bleeding_high",
        "name_de",
        "Hohes Blutungsrisiko",
    )
    import_reference_catalog(source)
    risk_model = CATALOG_MODELS[ReferenceKind.RISK]
    assert (
        risk_model._base_manager.get(name="bleeding_high").name_de  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
        == "Hohes Blutungsrisiko"
    )
    exported = canonical(export_reference_catalog())
    assert exported[(ReferenceKind.RISK, "bleeding_high", None)]["name_de"] == (
        "Hohes Blutungsrisiko"
    )


def test_catalog_entries_without_name_de_import_null_and_export_unchanged() -> None:
    # Current lx_dtypes Label entries carry no name_de; the column stays NULL.
    source = select_catalog(snapshot(), {ReferenceKind.LABEL})
    import_reference_catalog(source)
    label_model = CATALOG_MODELS[ReferenceKind.LABEL]
    rows = list(label_model._base_manager.values_list("name_de", flat=True))
    assert rows and all(value is None for value in rows)
    exported = canonical(export_reference_catalog())
    labels = [
        record for key, record in exported.items() if key[0] == ReferenceKind.LABEL
    ]
    assert labels and all("name_de" not in record for record in labels)
    assert canonical(export_reference_catalog()) == canonical(source.payload)


@pytest.mark.parametrize(
    "module,version",
    [("dgvs_reporting", "0.1.0"), ("dgvs_reporting", "0.2.0"), ("coloreg", "0.3.0")],
)
def test_dependency_only_reference_catalogue_accepts_clinical_packages(
    packaged_registry: Path,
    module: str,
    version: str,
) -> None:
    from lx_dtypes.packages import load_registered_package
    from lx_dtypes.models.contracts.reference_catalog import (
        TERMINOLOGY_REFERENCE_MODELS,
    )
    from endoreg_db.services.reference_data.clinical_projection import (
        import_clinical_package,
    )

    del packaged_registry
    kb = load_registered_package("endoreg_reference", version="2.0.0")
    selected = catalog_snapshot(kb)
    assert selected.clinical_package is not None
    assert not any(
        record.kind in TERMINOLOGY_REFERENCE_MODELS
        for record in selected.payload.records
    )
    assert plan_reference_catalog(selected).can_import
    import_reference_catalog(selected)
    replay_plan = plan_reference_catalog(selected)
    assert replay_plan.can_import, replay_plan.model_dump_json()
    assert import_reference_catalog(selected).unchanged
    result = import_clinical_package(load_registered_package(module, version=version))
    assert result.can_import
    assert import_reference_catalog(selected).unchanged


def test_catalogue_dependency_failure_rolls_back_application_records(
    packaged_registry: Path,
) -> None:
    from lx_dtypes.packages import load_registered_package
    from endoreg_db.models.other.unit import Unit

    del packaged_registry
    selected = catalog_snapshot(
        load_registered_package("endoreg_reference", version="2.0.0")
    )
    Unit.objects.create(
        name="miligram", abbreviation="wrong", description="conflicting unit"
    )
    plan = plan_reference_catalog(selected)
    assert not plan.can_import
    assert plan.clinical_plan is not None
    with pytest.raises(ValueError, match="conflicts"):
        import_reference_catalog(selected)
    assert not ReferenceCatalogImport.objects.exists()
    assert Unit.objects.get(name="miligram").abbreviation == "wrong"
