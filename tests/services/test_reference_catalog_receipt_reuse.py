"""Unchanged catalogues are recognised by receipt digest instead of a full row comparison."""

from io import StringIO
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
from lx_dtypes.knowledge_bases import get_packaged_knowledge_base
from lx_dtypes.models.contracts.knowledge_base import KnowledgeBaseIdentity
from lx_dtypes.terminology.terminology_loader import get_terminology_service

from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.config.settings import dataloader
from tests.helpers import data_loader
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


def _gender_snapshot() -> ReferenceCatalogSnapshot:
    root = get_packaged_knowledge_base(
        "endoreg_reference", "2.0.0"
    ).installed_data_root()
    kb = DataLoader(input_dirs=[root]).load_knowledge_base("endoreg_reference")
    return select_catalog(catalog_snapshot(kb), {ReferenceKind.GENDER})


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
    source = _gender_snapshot()
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
    source = _gender_snapshot()
    candidate = source
    if state != "no_receipt":
        import_reference_catalog(source)
    if state == "deleted_row":
        unit = source.payload.records[0]
        CATALOG_MODELS[ReferenceKind.GENDER]._base_manager.filter(
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
        reuse_current_catalog(_gender_snapshot())


def test_repeated_command_skips_full_comparison() -> None:
    call_command("load_gender_data", module_version="2.0.0", stdout=StringIO())
    receipts = ReferenceCatalogImport.objects.count()

    with patch(f"{_IMPORT_COMMAND}.plan_reference_catalog", _full_comparison_forbidden):
        output = StringIO()
        call_command("load_gender_data", module_version="2.0.0", stdout=output)

    assert '"unchanged": true' in output.getvalue()
    assert ReferenceCatalogImport.objects.count() == receipts


def test_clinical_helpers_share_durable_receipt() -> None:
    data_loader.load_examination_data()
    receipts = ReferenceCatalogImport.objects.count()
    with (
        patch(f"{_IMPORT_COMMAND}.plan_reference_catalog", _full_comparison_forbidden),
        patch(
            "endoreg_db.services.reference_data.catalog.plan_reference_catalog",
            _full_comparison_forbidden,
        ),
    ):
        data_loader.load_finding_data()
        data_loader.load_examination_indication_data()
        data_loader.load_examination_data()
    assert ReferenceCatalogImport.objects.count() == receipts
    assert ClinicalReferenceImport.objects.count() == 1


def test_clinical_verify_detects_drift_after_receipt_reuse() -> None:
    data_loader.load_examination_data()
    model = CATALOG_MODELS[ReferenceKind.EXAMINATION]
    row = model._base_manager.first()
    assert row is not None
    model._base_manager.filter(pk=row.pk).update(description="changed locally")
    data_loader.load_examination_data()
    package = dataloader.CLINICAL_PACKAGE
    with pytest.raises(CommandError, match="conflicts"):
        call_command(
            "load_examination_data",
            module=package.knowledge_base_module,
            module_version=package.knowledge_base_version,
            verify=True,
            stdout=StringIO(),
        )


@pytest.mark.parametrize("missing", ["receipt", "row"])
def test_missing_clinical_state_prevents_reuse(missing: str) -> None:
    data_loader.load_examination_data()
    package = dataloader.CLINICAL_PACKAGE
    snapshot = catalog_snapshot(
        get_terminology_service().load(
            package.knowledge_base_module, package.knowledge_base_version
        )
    )
    assert _reuse(snapshot) is not None
    if missing == "receipt":
        ClinicalReferenceImport.objects.all().delete()
    else:
        CATALOG_MODELS[ReferenceKind.EXAMINATION]._base_manager.all().delete()
    assert _reuse(snapshot) is None


@pytest.mark.parametrize(
    "module,version", [("another_module", "2.0.0"), ("endoreg_reference", "2.1.0")]
)
def test_changed_identity_does_not_reuse_receipt(module: str, version: str) -> None:
    source = _gender_snapshot()
    import_reference_catalog(source)
    identity = KnowledgeBaseIdentity(
        knowledge_base_module=module, knowledge_base_version=version
    )
    changed = ReferenceCatalogSnapshot(
        identity=identity,
        payload=source.payload,
        projection=source.projection,
        snapshot_id=ReferenceCatalogSnapshot.content_digest(
            identity, source.payload, source.projection
        ),
    )
    assert _reuse(changed) is None
    import_reference_catalog(changed)
    assert _reuse(changed) is not None
    assert ReferenceCatalogImport.objects.count() == 2


def test_verify_detects_field_drift_that_reuse_tolerates() -> None:
    call_command("load_gender_data", module_version="2.0.0", stdout=StringIO())
    unit = _gender_snapshot().payload.records[0]
    row = CATALOG_MODELS[ReferenceKind.GENDER]._base_manager.get(
        name=unit.identity.name
    )
    setattr(row, "description", "changed locally")
    row.save()

    call_command("load_gender_data", module_version="2.0.0", stdout=StringIO())
    with pytest.raises(CommandError, match="conflicts"):
        call_command(
            "load_gender_data", module_version="2.0.0", verify=True, stdout=StringIO()
        )
    row.refresh_from_db()
    assert getattr(row, "description") == "changed locally"


def test_repeated_reconcile_startup_does_not_invoke_historical_migration() -> None:
    call_command(
        "load_base_db_data",
        module_version="2.0.0",
        reconcile_legacy=True,
        stdout=StringIO(),
    )

    with (
        patch(f"{_MIGRATION}.plan_catalog_migration", _full_comparison_forbidden),
        patch(f"{_MIGRATION}.plan_reference_catalog", _full_comparison_forbidden),
    ):
        output = StringIO()
        call_command(
            "load_base_db_data",
            module_version="2.0.0",
            reconcile_legacy=True,
            stdout=output,
        )

    assert '"unchanged": true' in output.getvalue()
    assert ReferenceCatalogImport.objects.count() == 1
