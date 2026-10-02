from collections import Counter
from importlib.resources import files
from io import StringIO
from pathlib import Path
from typing import Unpack
from unittest.mock import patch

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from lx_dtypes.models.interface.DataLoader import DataLoader
from lx_dtypes.models.contracts.reference_catalog import ReferenceKind
from lx_dtypes.models.contracts.reference_catalog_snapshot import (
    ReferenceCatalogSnapshot,
)

from endoreg_db.models import (
    Examination,
    ExaminationIndication,
    FindingIntervention,
    Patient,
    PatientExamination,
    PatientExaminationIndication,
)
from endoreg_db.helpers.typing import DjangoModelSaveKwargs
from endoreg_db.models.other.reference_catalog_import import ReferenceCatalogImport
from endoreg_db.services.reference_data.catalog import (
    catalog_snapshot,
    import_reference_catalog,
    plan_reference_catalog,
)
from endoreg_db.services.reference_data.catalog_export import export_reference_catalog
from endoreg_db.services.reference_data.catalog_migration import (
    apply_catalog_migration,
    migration_spec,
    plan_catalog_migration,
)
from endoreg_db.services.reference_data.catalog_models import CATALOG_MODELS

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("legacy", [False, True])
def test_startup_loader_imports_clean_or_equivalent_catalogue_idempotently(
    legacy: bool,
) -> None:
    if legacy:
        call_command("load_base_db_data", stdout=StringIO())
        ReferenceCatalogImport.objects.all().delete()
    for _ in range(2):
        call_command("load_base_db_data", reconcile_legacy=True, stdout=StringIO())
        assert len(export_reference_catalog().records) == 673
        assert ReferenceCatalogImport.objects.count() == 1


@pytest.mark.parametrize("blocked", [False, True])
def test_startup_loader_reconciles_legacy_and_preserves_failure_boundary(
    legacy_catalogue: ReferenceCatalogSnapshot, blocked: bool
) -> None:
    if blocked:
        row = ExaminationIndication.objects.get(name=migration_spec().indications[0])
        row.description = "Unrelated local change"
        row.save()
    before = export_reference_catalog()
    with pytest.raises(CommandError):
        call_command("load_base_db_data", stdout=StringIO())
    if blocked:
        with pytest.raises(CommandError, match="blockers"):
            call_command("load_base_db_data", reconcile_legacy=True, stdout=StringIO())
        assert export_reference_catalog() == before
        assert not ReferenceCatalogImport.objects.exists()
        return
    for _ in range(2):
        output = StringIO()
        call_command(
            "load_base_db_data", reconcile_legacy=True, format="yml", stdout=output
        )
        assert yaml.safe_load(output.getvalue())["unchanged"] is True
        assert plan_reference_catalog(legacy_catalogue).unchanged
        assert ReferenceCatalogImport.objects.count() == 2


def test_startup_loader_dry_run_never_hydrates_or_writes(
    legacy_catalogue: ReferenceCatalogSnapshot, packaged_registry: Path
) -> None:
    # Provision through the existing loader, which fails strict legacy adoption.
    with pytest.raises(CommandError):
        call_command("load_base_db_data", stdout=StringIO())
    registry_before = packaged_registry.read_bytes()
    before = export_reference_catalog()
    with patch(
        "endoreg_db.management.commands.load_base_db_data.hydrate_shipped_terminology"
    ) as hydrate:
        call_command(
            "load_base_db_data", reconcile_legacy=True, dry_run=True, stdout=StringIO()
        )
        hydrate.assert_not_called()
    assert packaged_registry.read_bytes() == registry_before
    assert export_reference_catalog() == before
    assert not ReferenceCatalogImport.objects.exists()


def test_startup_loader_rejects_other_catalogue_before_hydration() -> None:
    with patch(
        "endoreg_db.management.commands.load_base_db_data.hydrate_shipped_terminology"
    ) as hydrate:
        with pytest.raises(CommandError, match="requires endoreg_reference"):
            call_command(
                "load_base_db_data",
                reconcile_legacy=True,
                module="coloreg",
                stdout=StringIO(),
            )
        hydrate.assert_not_called()
    assert not ReferenceCatalogImport.objects.exists()


@pytest.fixture
def legacy_catalogue() -> ReferenceCatalogSnapshot:
    source = catalog_snapshot(
        DataLoader(
            input_dirs=[Path(str(files("lx_dtypes").joinpath("data")))]
        ).load_knowledge_base("endoreg_reference")
    )
    spec = migration_spec()
    assert source.snapshot_id == spec.snapshot_id
    import_reference_catalog(source)
    ReferenceCatalogImport.objects.all().delete()
    CATALOG_MODELS[ReferenceKind.FINDING_INTERVENTION]._base_manager.filter(
        name__in=spec.interventions
    ).delete()
    CATALOG_MODELS[ReferenceKind.FINDING_INTERVENTION_TYPE]._base_manager.filter(
        name__in=spec.intervention_types
    ).delete()
    return source


def test_exact_gc02_plan_is_read_only_and_strict_adoption_still_rejects(
    legacy_catalogue: ReferenceCatalogSnapshot,
) -> None:
    before = export_reference_catalog()
    plan = plan_reference_catalog(legacy_catalogue, adopt_existing=True)
    assert Counter(change.action for change in plan.changes) == {
        "adopt": 657,
        "create": 9,
        "conflict": 7,
    }
    with pytest.raises(ValueError, match="conflicts"):
        import_reference_catalog(legacy_catalogue, adopt_existing=True)
    review = plan_catalog_migration(legacy_catalogue)
    assert review.can_apply
    assert {change.before.name for change in review.relationships} == set(
        migration_spec().indications
    )
    assert all(
        set(c.before.expected_interventions) < set(c.after.expected_interventions)
        for c in review.relationships
    )
    assert export_reference_catalog() == before
    assert not ReferenceCatalogImport.objects.exists()


def test_migration_preserves_existing_ids_patient_links_and_is_idempotent(
    legacy_catalogue: ReferenceCatalogSnapshot,
) -> None:
    before = {
        kind: set(model._base_manager.values_list("pk", flat=True))
        for kind, model in CATALOG_MODELS.items()
    }
    examination = Examination.objects.get(name="colonoscopy")
    patient = Patient.objects.create(
        first_name="Migration",
        last_name="Test",
        is_real_person=False,
        patient_hash="catalogue-migration-test",
    )
    capture = PatientExamination.objects.create(
        patient=patient, examination=examination
    )
    indication = ExaminationIndication.objects.get(name=migration_spec().indications[0])
    patient_indication = PatientExaminationIndication.objects.create(
        patient_examination=capture, examination_indication=indication
    )
    apply_catalog_migration(legacy_catalogue)
    patient_indication.refresh_from_db()
    assert patient_indication.examination_indication == indication
    assert patient_indication.patient_examination == capture
    assert plan_reference_catalog(legacy_catalogue).unchanged
    assert apply_catalog_migration(legacy_catalogue).unchanged
    assert all(
        ids <= set(CATALOG_MODELS[kind]._base_manager.values_list("pk", flat=True))
        for kind, ids in before.items()
    )
    capture.refresh_from_db()
    assert (capture.patient_id, capture.examination_id) == (patient.pk, examination.pk)
    assert capture.knowledge_base_module == capture.knowledge_base_version == ""
    assert len(export_reference_catalog().records) == 673


@pytest.mark.parametrize(
    "drift",
    ["description", "unexpected_link", "missing_generic_link", "missing_record"],
)
def test_other_database_differences_are_blockers_without_writes(
    legacy_catalogue: ReferenceCatalogSnapshot, drift: str
) -> None:
    row = ExaminationIndication.objects.get(name=migration_spec().indications[0])
    if drift == "description":
        row.description = "Locally reviewed different meaning"
        row.save()
    elif drift == "unexpected_link":
        other = FindingIntervention.objects.exclude(
            pk__in=row.expected_interventions.values("pk")
        ).first()
        assert other is not None
        row.expected_interventions.add(other)
    elif drift == "missing_generic_link":
        row.expected_interventions.clear()
    else:
        deleted, _ = (
            CATALOG_MODELS[ReferenceKind.UNIT]
            ._base_manager.filter(name="kilogram")
            .delete()
        )
        assert deleted > 0
    before = export_reference_catalog()
    review = plan_catalog_migration(legacy_catalogue)
    assert not review.can_apply
    with pytest.raises(ValueError, match="blockers"):
        apply_catalog_migration(legacy_catalogue)
    assert export_reference_catalog() == before
    assert not ReferenceCatalogImport.objects.exists()


def test_changed_catalogue_is_rejected_without_writes(
    legacy_catalogue: ReferenceCatalogSnapshot,
) -> None:
    before = export_reference_catalog()
    altered = legacy_catalogue.model_copy(update={"snapshot_id": "sha256:" + "0" * 64})
    with pytest.raises(ValueError, match="content digest"):
        apply_catalog_migration(altered)
    assert export_reference_catalog() == before
    assert not ReferenceCatalogImport.objects.exists()


def test_final_receipt_failure_rolls_back_dependencies_and_relationships(
    legacy_catalogue: ReferenceCatalogSnapshot,
) -> None:
    before = export_reference_catalog()
    original_save = ReferenceCatalogImport.save

    def fail_full_receipt(
        self: ReferenceCatalogImport,
        *args: object,
        **kwargs: Unpack[DjangoModelSaveKwargs],
    ) -> None:
        if self.projection == "all":
            raise ValueError("injected final receipt failure")
        return original_save(self, *args, **kwargs)

    with patch.object(ReferenceCatalogImport, "save", fail_full_receipt):
        with pytest.raises(ValueError, match="final receipt"):
            apply_catalog_migration(legacy_catalogue)
    assert export_reference_catalog() == before
    assert not ReferenceCatalogImport.objects.exists()


@pytest.mark.parametrize("blocked", [False, True])
def test_cli_dry_run_and_single_command_apply(
    legacy_catalogue: ReferenceCatalogSnapshot, blocked: bool
) -> None:
    from lx_dtypes.terminology.terminology_loader import (
        get_terminology_service,
        hydrate_shipped_terminology,
    )

    hydrate_shipped_terminology()
    service = get_terminology_service()
    spec = migration_spec()
    assert catalog_snapshot(service.load(spec.module, spec.version)) == legacy_catalogue
    if blocked:
        row = ExaminationIndication.objects.get(name=spec.indications[0])
        row.description = "Different clinical meaning"
        row.save()
    before = export_reference_catalog()
    if blocked:
        for dry_run in (True, False):
            with pytest.raises(CommandError, match="blockers"):
                call_command(
                    "migrate_reference_catalog", dry_run=dry_run, stdout=StringIO()
                )
            assert export_reference_catalog() == before
            assert not ReferenceCatalogImport.objects.exists()
        return
    output = StringIO()
    call_command("migrate_reference_catalog", dry_run=True, stdout=output)
    assert '"relationships"' in output.getvalue()
    assert export_reference_catalog() == before
    assert not ReferenceCatalogImport.objects.exists()
    call_command("migrate_reference_catalog", stdout=StringIO())
    assert plan_reference_catalog(legacy_catalogue).unchanged
    receipts = ReferenceCatalogImport.objects.count()
    call_command("migrate_reference_catalog", stdout=StringIO())
    assert ReferenceCatalogImport.objects.count() == receipts


def test_migration_does_not_rewrite_previously_imported_clinical_meaning(
    legacy_catalogue: ReferenceCatalogSnapshot,
) -> None:
    apply_catalog_migration(legacy_catalogue)
    row = ExaminationIndication.objects.get(name=migration_spec().indications[0])
    row.expected_interventions.remove(
        FindingIntervention.objects.get(name=migration_spec().interventions[0])
    )
    # Row drift under an existing immutable receipt must never be repaired by this migration.
    reviewed = plan_catalog_migration(legacy_catalogue)
    assert not reviewed.can_apply
    before = export_reference_catalog()
    with pytest.raises(ValueError, match="blockers"):
        apply_catalog_migration(legacy_catalogue)
    assert export_reference_catalog() == before
