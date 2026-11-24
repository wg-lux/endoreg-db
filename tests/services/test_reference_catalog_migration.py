from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Unpack
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from lx_dtypes.models.interface.DataLoader import DataLoader
from lx_dtypes.knowledge_bases import get_packaged_knowledge_base
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


@pytest.mark.parametrize("command", ["load_base_db_data", "migrate_reference_catalog"])
def test_current_reconciliation_is_idempotent_and_preserves_conflicting_rows(
    command: str,
) -> None:
    from endoreg_db.models import Unit

    call_command(command, stdout=StringIO())
    receipt = ReferenceCatalogImport.objects.get()
    assert receipt.version == "2.0.0"
    before = export_reference_catalog()
    call_command(command, stdout=StringIO())
    assert export_reference_catalog() == before
    unit = Unit.objects.get(name="miligram")
    unit.description = "Locally reviewed conflicting meaning"
    unit.save(update_fields=["description"])
    before = export_reference_catalog()
    with pytest.raises(CommandError, match="conflicts"):
        call_command(command, stdout=StringIO())
    assert export_reference_catalog() == before


def test_current_reconciliation_dry_run_does_not_hydrate_or_write(
    packaged_registry: Path,
) -> None:
    registry_before = packaged_registry.read_bytes()
    with patch(
        "endoreg_db.management.reference_catalog_command.hydrate_shipped_terminology"
    ) as hydrate:
        call_command(
            "load_base_db_data", reconcile_legacy=True, dry_run=True, stdout=StringIO()
        )
        hydrate.assert_not_called()
    assert packaged_registry.read_bytes() == registry_before
    assert not ReferenceCatalogImport.objects.exists()


@pytest.fixture
def legacy_catalogue() -> ReferenceCatalogSnapshot:
    source = catalog_snapshot(
        DataLoader(
            input_dirs=[
                get_packaged_knowledge_base(
                    "endoreg_reference", "1.0.0"
                ).installed_data_root()
            ]
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
    # The receipt is current and every bound row exists, so the default startup
    # path reuses it without writes; only the explicit full check reports drift.
    assert apply_catalog_migration(legacy_catalogue).unchanged
    assert export_reference_catalog() == before
    with pytest.raises(ValueError, match="blockers"):
        apply_catalog_migration(legacy_catalogue, verify=True)
    assert export_reference_catalog() == before
