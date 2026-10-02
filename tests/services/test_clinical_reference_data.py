from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connections
from lx_dtypes.models.interface.DataLoader import DataLoader
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
)
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.medical.finding.finding_intervention import FindingIntervention
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.schemas.clinical_reference import ClinicalReferenceReceipt
from endoreg_db.services.reference_data.clinical_projection import (
    clinical_snapshot,
    import_clinical_references,
    plan_clinical_reference_import,
)

pytestmark = pytest.mark.django_db


def package(
    *, version: str = "1.0.0", description: str = "Clinical inspection"
) -> KnowledgeBase:
    return KnowledgeBase.model_validate(
        {
            "config": {"name": "clinical_test", "version": version},
            "intervention_type": {"sampling": {"name": "sampling"}},
            "intervention": {
                "biopsy": {"name": "biopsy", "intervention_types": ["sampling"]}
            },
            "finding": {"lesion": {"name": "lesion", "interventions": ["biopsy"]}},
            "indication_type": {"diagnostic": {"name": "diagnostic"}},
            "indication": {
                "investigation": {
                    "name": "investigation",
                    "indication_types": ["diagnostic"],
                    "interventions": ["biopsy"],
                }
            },
            "examination": {
                "inspection": {
                    "name": "inspection",
                    "description": description,
                    "findings": ["lesion"],
                    "indications": ["investigation"],
                }
            },
        }
    )


def test_dry_run_import_repeat_and_lossless_snapshot() -> None:
    snapshot = clinical_snapshot(package())
    plan = plan_clinical_reference_import(snapshot)
    assert plan.can_import
    assert {change.action for change in plan.changes} == {"create"}
    assert not Examination.objects.exists()
    assert not ClinicalReferenceImport.objects.exists()
    assert import_clinical_references(snapshot) == plan
    examination = Examination.objects.get(name="inspection")
    indication = ExaminationIndication.objects.get(name="investigation")
    assert list(examination.findings.values_list("name", flat=True)) == ["lesion"]
    assert list(indication.expected_interventions.values_list("name", flat=True)) == [
        "biopsy"
    ]
    assert import_clinical_references(snapshot).unchanged
    assert Examination.objects.get(name="inspection").pk == examination.pk
    receipt = ClinicalReferenceReceipt.model_validate(
        ClinicalReferenceImport.objects.get().payload
    )
    assert receipt.snapshot == snapshot
    assert len(receipt.bindings) == len(plan.changes)


def test_final_receipt_failure_rolls_back_nodes_and_links() -> None:
    with patch.object(
        ClinicalReferenceImport,
        "save",
        side_effect=ValueError("injected final failure"),
    ):
        with pytest.raises(ValueError, match="injected final failure"):
            import_clinical_references(clinical_snapshot(package()))
    assert not Examination.objects.exists()
    assert not Finding.objects.exists()
    assert not FindingIntervention.objects.exists()
    assert not ClinicalReferenceImport.objects.exists()


def test_identity_reuse_and_row_drift_fail_without_repair() -> None:
    snapshot = clinical_snapshot(package())
    import_clinical_references(snapshot)
    with pytest.raises(ValueError, match="conflicts"):
        import_clinical_references(
            clinical_snapshot(package(description="Changed meaning"))
        )
    Examination.objects.filter(name="inspection").update(description="Local change")
    plan = plan_clinical_reference_import(snapshot)
    assert not plan.can_import
    with pytest.raises(ValueError, match="conflicts"):
        import_clinical_references(snapshot)
    assert Examination.objects.get().description == "Local change"


def test_legacy_adoption_is_explicit_and_preserves_primary_keys() -> None:
    original = FindingIntervention.objects.create(name="biopsy", description="")
    # Deliberately missing the authored type relation: adoption cannot rewrite it.
    snapshot = clinical_snapshot(package())
    assert not plan_clinical_reference_import(snapshot, adopt_existing=True).can_import
    assert FindingIntervention.objects.get().pk == original.pk
    assert not Examination.objects.exists()


def test_equivalent_legacy_row_adoption_does_not_claim_historical_origin() -> None:
    kb = KnowledgeBase.model_validate(
        {
            "config": {"name": "plain", "version": "1.0.0"},
            "examination": {"inspection": {"name": "inspection", "description": ""}},
        }
    )
    row = Examination.objects.create(name="inspection", description="")
    snapshot = clinical_snapshot(kb)
    assert not plan_clinical_reference_import(snapshot).can_import
    import_clinical_references(snapshot, adopt_existing=True)
    binding = ClinicalReferenceReceipt.model_validate(
        ClinicalReferenceImport.objects.get().payload
    ).bindings[0]
    assert binding.row_id == row.pk
    assert binding.origin == "legacy_equivalent"


def test_direct_receipt_mutation_is_rejected() -> None:
    import_clinical_references(clinical_snapshot(package()))
    row = ClinicalReferenceImport.objects.get()
    row.version = "2.0.0"
    with pytest.raises(ValidationError):
        row.save()


@pytest.mark.django_db(transaction=True)
def test_concurrent_imports_create_one_generation() -> None:
    snapshot = clinical_snapshot(package())

    def run() -> None:
        try:
            import_clinical_references(snapshot)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        for future in futures:
            future.result(timeout=60)
    assert ClinicalReferenceImport.objects.count() == 1
    assert Examination.objects.filter(name="inspection").count() == 1


def test_real_coloreg_closure_provisions_indications_and_interventions_without_legacy_loader() -> (
    None
):
    loader = DataLoader(input_dirs=[Path(str(files("lx_dtypes").joinpath("data")))])
    loader.load_module_configs()
    kb = loader.load_knowledge_base("coloreg")
    snapshot = clinical_snapshot(kb)
    import_clinical_references(snapshot)
    assert ExaminationIndication.objects.count() == 14
    for indication in snapshot.concepts.indication:
        row = ExaminationIndication.objects.get(name=indication.name)
        assert set(row.expected_interventions.values_list("name", flat=True)) == set(
            indication.interventions
        )
    assert Examination.objects.filter(name="endoscopic_ultrasound").exists()
    assert FindingIntervention.objects.count() == len(snapshot.concepts.intervention)
    assert import_clinical_references(snapshot).unchanged


def test_command_dry_run_uses_explicit_shared_registry_identity() -> None:
    with patch(
        "endoreg_db.management.commands.import_clinical_reference_data.get_terminology_service"
    ) as service:
        service.return_value.load.return_value = package()
        call_command(
            "import_clinical_reference_data",
            module="clinical_test",
            module_version="1.0.0",
            dry_run=True,
            stdout=StringIO(),
        )
        service.return_value.load.assert_called_once_with("clinical_test", "1.0.0")
    assert not Examination.objects.exists()


def test_changed_transitive_meaning_cannot_reuse_a_row_in_another_version() -> None:
    import_clinical_references(clinical_snapshot(package()))
    changed = package(version="2.0.0")
    changed.intervention["biopsy"].description = "Changed intervention definition"
    plan = plan_clinical_reference_import(clinical_snapshot(changed))
    examination_change = next(c for c in plan.changes if c.name == "inspection")
    assert examination_change.action == "conflict"
    assert "immutable_clinical_meaning" in examination_change.differing_fields
    with pytest.raises(ValueError, match="conflicts"):
        import_clinical_references(clinical_snapshot(changed))
    assert ClinicalReferenceImport.objects.count() == 1


def test_equivalent_new_version_preserves_existing_rows_and_both_snapshots() -> None:
    import_clinical_references(clinical_snapshot(package()))
    row_id = Examination.objects.get(name="inspection").pk
    import_clinical_references(clinical_snapshot(package(version="2.0.0")))
    assert ClinicalReferenceImport.objects.count() == 2
    assert Examination.objects.get(name="inspection").pk == row_id


def test_legacy_bootstrap_reconciliation_is_read_only_and_rejects_semantic_changes() -> (
    None
):
    call_command("load_base_db_data", stdout=StringIO())
    before = dict(Examination.objects.values_list("name", "id"))
    loader = DataLoader(input_dirs=[Path(str(files("lx_dtypes").joinpath("data")))])
    loader.load_module_configs()
    snapshot = clinical_snapshot(loader.load_knowledge_base("coloreg"))
    plan = plan_clinical_reference_import(snapshot, adopt_existing=True)
    conflicts = [change for change in plan.changes if change.action == "conflict"]
    assert conflicts
    with pytest.raises(ValueError, match="conflicts"):
        import_clinical_references(snapshot, adopt_existing=True)
    assert dict(Examination.objects.values_list("name", "id")) == before
    assert not ClinicalReferenceImport.objects.exists()
    print(
        "Legacy reconciliation:",
        {
            action: sum(c.action == action for c in plan.changes)
            for action in ("create", "adopt", "reuse", "conflict")
        },
    )
    print(
        "Clinical conflicts:",
        [(c.model.value, c.name, c.differing_fields) for c in conflicts[:10]],
    )
