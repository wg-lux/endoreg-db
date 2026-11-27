from concurrent.futures import ThreadPoolExecutor
from importlib.resources import files
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from lx_dtypes.models.interface.DataLoader import DataLoader
from lx_dtypes.models.interface.KnowledgeBase import KnowledgeBase
from lx_dtypes.models.knowledge_base.examination import (
    Examination as ExaminationDefinition,
)
from lx_dtypes.packages import create_package
from lx_dtypes.packages import load_package, load_registered_package
from lx_dtypes.models.contracts.package_snapshot import build_package_snapshot
from lx_dtypes.models.contracts.reference_catalog import CatalogIdentity, ReferenceKind
from lx_dtypes.models.contracts.reference_migration import (
    ReferenceMigrationCatalog,
    ReferenceMigrationRule,
    ReferenceMigrationTarget,
)
from lx_dtypes.models.knowledge_base.pydantic_main import KB_MODEL_NAMES_ORDERED
from lx_dtypes.models.knowledge_base.study_preset import StudyPreset
from lx_dtypes.models.knowledge_base.electrical_curve_profile import (
    ElectricalCurveProfile,
)
from lx_dtypes.models.knowledge_base.time_reference import TimeReference
from lx_dtypes.models.contracts.time import (
    ElectrosurgeryTimestamp,
    PtsTimestamp,
    TimeOffset,
)
from lx_dtypes.models.contracts.time_reference import (
    TimeOffsetEvidence,
    TimeOffsetExample,
    TimeVideoReference,
)
from lx_dtypes.models.contracts.video_segment_timeline import (
    VideoSegmentGenerationCoordinates,
    VideoSegmentTimelineCoordinates,
)
from lx_dtypes.models.ledger.p_time_offset import PTimeOffset

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
    import_clinical_package,
    load_imported_package,
    plan_clinical_reference_import,
)

pytestmark = pytest.mark.django_db


def package(
    *, version: str = "1.0.0", description: str = "Clinical inspection"
) -> KnowledgeBase:
    kb = KnowledgeBase.model_validate(
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
    for records in (
        kb.intervention_type,
        kb.intervention,
        kb.finding,
        kb.indication_type,
        kb.indication,
        kb.examination,
    ):
        for record in records.values():
            record.kb_module_name = kb.config.name
    return kb


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
    import_clinical_package(kb)
    restored = load_imported_package(kb.config.name, kb.config.version)
    assert (
        build_package_snapshot(restored).snapshot_id
        == build_package_snapshot(kb).snapshot_id
    )
    assert ExaminationIndication.objects.count() == 14
    for indication in snapshot.concepts.indication:
        row = ExaminationIndication.objects.get(name=indication.name)
        assert set(row.expected_interventions.values_list("name", flat=True)) == set(
            indication.interventions
        )
    assert Examination.objects.filter(name="endoscopic_ultrasound").exists()
    assert FindingIntervention.objects.count() == len(snapshot.concepts.intervention)
    assert import_clinical_references(snapshot).unchanged


def test_command_dry_run_uses_explicit_shared_registry_identity(tmp_path: Path) -> None:
    manifest = create_package(tmp_path / "empty", name="clinical_test", version="1.0.0")
    with patch(
        "endoreg_db.management.commands.import_clinical_reference_data.load_registered_package"
    ) as service:
        service.return_value = load_package(manifest)
        call_command(
            "import_clinical_reference_data",
            module="clinical_test",
            module_version="1.0.0",
            dry_run=True,
            stdout=StringIO(),
        )
        service.assert_called_once_with("clinical_test", version="1.0.0")
    assert not Examination.objects.exists()


def test_command_validates_local_package_and_rejects_identity_mismatch(
    tmp_path: Path,
) -> None:
    manifest = create_package(
        tmp_path / "clinical",
        name="clinical_test",
        version="1.0.0",
        records=[
            ExaminationDefinition(
                name="inspection", name_de="Untersuchung", name_en="Inspection"
            )
        ],
    )
    options = {
        "module": "clinical_test",
        "module_version": "1.0.0",
        "config_path": manifest,
        "stdout": StringIO(),
    }
    call_command("import_clinical_reference_data", dry_run=True, **options)
    assert not ClinicalReferenceImport.objects.exists()
    with pytest.raises(CommandError):
        call_command(
            "import_clinical_reference_data",
            module="wrong_module",
            module_version="1.0.0",
            config_path=manifest,
            stdout=StringIO(),
        )
    assert not ClinicalReferenceImport.objects.exists()
    call_command("import_clinical_reference_data", **options)
    assert Examination.objects.get().name == "inspection"
    restored = load_imported_package("clinical_test", "1.0.0")
    snapshot = build_package_snapshot(restored)
    assert set(snapshot.records) == set(KB_MODEL_NAMES_ORDERED)
    assert restored.examination["inspection"].name_de == "Untersuchung"
    assert import_clinical_package(load_package(manifest)).unchanged


def test_historical_graph_receipt_requires_explicit_complete_package_migration() -> (
    None
):
    import_clinical_references(clinical_snapshot(package()))
    with pytest.raises(ValueError, match="complete package snapshot"):
        load_imported_package("clinical_test", "1.0.0")


@pytest.mark.parametrize(
    "redefined_family", ["study_preset", "time_reference", "electrical_curve_profile"]
)
def test_full_package_receipt_preserves_non_graph_records_and_blocks_redefinition(
    tmp_path: Path, redefined_family: str
) -> None:
    evidence = TimeOffsetEvidence(
        offset=TimeOffset(
            reference=PtsTimestamp(
                kind="pts",
                clock_id="video",
                ticks=180_000,
                time_base_num=1,
                time_base_den=90_000,
            ),
            target=ElectrosurgeryTimestamp(
                kind="electrosurgery",
                clock_id="generator",
                ticks=2250,
                time_base_num=1,
                time_base_den=1000,
            ),
        ),
        video_references={
            "video": TimeVideoReference(
                coordinates=VideoSegmentTimelineCoordinates(
                    generation=VideoSegmentGenerationCoordinates(
                        content_hash="a" * 64,
                        time_base_num=1,
                        time_base_den=90_000,
                        first_frame_number=0,
                        last_frame_number=1,
                        first_presentation_timestamp=180_000,
                        last_presentation_timestamp=183_600,
                    ),
                    recording_mapping_status="unresolved",
                )
            )
        },
    )
    create_package(
        tmp_path / "timing_terms",
        name="timing_terms",
        version="1.0.0",
        records=[
            TimeReference(
                name="video_to_generator",
                name_de="Zeitbezug zwischen Video und Generator",
                reference_kind="pts",
                target_kind="electrosurgery",
                examples=[
                    TimeOffsetExample(
                        example_id="recorded_event",
                        role="validation",
                        evidence=evidence,
                    )
                ],
            ),
            ElectricalCurveProfile(
                name="power_response",
                name_de="Leistungsantwort",
                curve_quantities=["power", "voltage_peak"],
                response_quantities=["power"],
                predictor_quantities=["voltage_peak"],
                phase_bins=8,
                min_duration_num=1,
                min_duration_den=20,
                max_duration_num=30,
                max_duration_den=1,
                max_sample_gap_num=1,
                max_sample_gap_den=100,
                max_mean_mahalanobis_squared=2.5,
            ),
        ],
    )
    manifest = create_package(
        tmp_path / "preset",
        name="preset",
        version="1.0.0",
        depends_on=["timing_terms"],
        records=[StudyPreset(name="collection", name_de="Studiensammlung")],
    )
    kb = load_package(manifest)
    import_clinical_package(kb)
    restored = load_imported_package("preset", "1.0.0")
    assert restored.study_preset["collection"].model_dump(
        exclude={"source_file"}
    ) == kb.study_preset["collection"].model_dump(exclude={"source_file"})
    assert restored.time_reference["video_to_generator"].model_dump() == (
        kb.time_reference["video_to_generator"].model_dump()
    )
    assert restored.electrical_curve_profile["power_response"].model_dump() == (
        kb.electrical_curve_profile["power_response"].model_dump()
    )
    observation = PTimeOffset(
        patient_examination="test_examination",
        time_reference="video_to_generator",
        knowledge_base_module="timing_terms",
        knowledge_base_version="1.0.0",
        evidence=evidence,
    )
    reloaded_observation = PTimeOffset.model_validate_json(
        observation.model_dump_json()
    )
    restored.validate_time_offset(reloaded_observation)
    assert reloaded_observation.evidence == evidence
    assert import_clinical_package(load_package(manifest)).unchanged
    changed = load_package(manifest)
    {
        "study_preset": changed.study_preset["collection"],
        "time_reference": changed.time_reference["video_to_generator"],
        "electrical_curve_profile": changed.electrical_curve_profile["power_response"],
    }[redefined_family].description = "Changed definition"
    assert clinical_snapshot(changed) == clinical_snapshot(kb)
    with pytest.raises(ValueError, match="conflicts"):
        import_clinical_package(changed)
    assert (
        build_package_snapshot(load_imported_package("preset", "1.0.0")).snapshot_id
        == build_package_snapshot(kb).snapshot_id
    )


def test_changed_transitive_meaning_cannot_reuse_a_row_in_another_version() -> None:
    baseline = package()
    baseline.intervention["biopsy"].description = "Original intervention definition"
    import_clinical_references(clinical_snapshot(baseline))
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


@pytest.mark.parametrize(
    "bootstrap", ["development", "reference_catalog", "dgvs_reporting_0_1_0"]
)
def test_legacy_bootstrap_reconciliation_is_read_only_and_rejects_semantic_changes(
    bootstrap: str,
    packaged_registry: Path,
) -> None:
    del packaged_registry
    if bootstrap == "development":
        call_command("load_base_db_data", module_version="1.0.0", stdout=StringIO())
    elif bootstrap == "dgvs_reporting_0_1_0":
        import_clinical_package(
            load_registered_package("dgvs_reporting", version="0.1.0")
        )
    else:
        from endoreg_db.services.reference_data.catalog import (
            catalog_snapshot,
            import_reference_catalog,
        )

        from lx_dtypes.knowledge_bases import get_packaged_knowledge_base

        root = get_packaged_knowledge_base(
            "endoreg_reference", "1.0.0"
        ).installed_data_root()
        import_reference_catalog(
            catalog_snapshot(
                DataLoader(input_dirs=[root]).load_knowledge_base("endoreg_reference")
            )
        )
    before = dict(Examination.objects.values_list("name", "id"))
    before_receipts = list(
        ClinicalReferenceImport.objects.values_list("payload", flat=True)
    )
    snapshot = clinical_snapshot(load_registered_package("coloreg", version="0.3.0"))
    plan = plan_clinical_reference_import(snapshot, adopt_existing=True)
    conflicts = [change for change in plan.changes if change.action == "conflict"]
    if bootstrap == "dgvs_reporting_0_1_0":
        # Finding membership is package-specific; genuine descriptor changes
        # between historical releases still block shared clinical rows.
        assert not any(item.name == "colonoscopy" for item in conflicts)
    assert conflicts
    assert not any(
        detail.field == "description"
        and detail.existing in (None, "", "unknown")
        and detail.proposed in (None, "", "unknown")
        for change in conflicts
        for detail in change.differences
    )
    with pytest.raises(ValueError, match="conflicts"):
        import_clinical_references(snapshot, adopt_existing=True)
    assert dict(Examination.objects.values_list("name", "id")) == before
    assert (
        list(ClinicalReferenceImport.objects.values_list("payload", flat=True))
        == before_receipts
    )
    print(
        "Legacy reconciliation:",
        {
            action: sum(c.action == action for c in plan.changes)
            for action in ("create", "adopt", "reuse", "conflict")
        },
    )
    import json

    print(
        "ALIGNMENT_REPORT:"
        + json.dumps(
            {
                "baseline": bootstrap,
                "package": "coloreg@0.3.0",
                "snapshot_id": snapshot.snapshot_id,
                "conflicts": [
                    change.model_dump(mode="json", exclude={"row_id"})
                    for change in conflicts
                ],
            }
        )
    )


@pytest.mark.parametrize("existing,proposed", [("", "unknown"), ("unknown", "")])
def test_placeholder_description_is_equivalent_without_rewriting_receipts(
    existing: str, proposed: str
) -> None:
    first = clinical_snapshot(package(description=existing))
    import_clinical_references(first)
    before = list(ClinicalReferenceImport.objects.values_list("payload", flat=True))
    next_snapshot = clinical_snapshot(package(version="2.0.0", description=proposed))
    plan = plan_clinical_reference_import(next_snapshot)
    assert plan.can_import
    import_clinical_references(next_snapshot)
    assert Examination.objects.get(name="inspection").description == existing
    assert (
        list(
            ClinicalReferenceImport.objects.order_by("pk").values_list(
                "payload", flat=True
            )
        )[:1]
        == before
    )


def test_terminology_upgrade_applies_and_records_safe_changes_once() -> None:
    baseline = package(description="unknown")
    baseline.examination["inspection"].name_de = "Alt"
    import_clinical_package(baseline)
    row = Examination.objects.get(name="inspection")
    old_pk = row.pk
    old_receipt = ClinicalReferenceImport.objects.get().payload

    updated = package(version="2.0.0")
    updated.examination["inspection"].name_de = "Neu"
    updated.finding["lesion"].description = "Additional finding description."
    plan = plan_clinical_reference_import(clinical_snapshot(updated))
    assert plan.can_import
    assert sum(change.migration is not None for change in plan.changes) == 2
    row.refresh_from_db()
    assert (row.description, row.name_de) == ("unknown", "Alt")
    assert not plan.applied_migrations

    applied = import_clinical_package(updated)
    assert {
        item.identity.name: item.updates for item in applied.applied_migrations
    } == {
        "inspection": {"description": "Clinical inspection", "name_de": "Neu"},
        "lesion": {"description": "Additional finding description."},
    }
    row.refresh_from_db()
    assert (row.pk, row.description, row.name_de) == (
        old_pk,
        "Clinical inspection",
        "Neu",
    )
    assert ClinicalReferenceImport.objects.get(version="1.0.0").payload == old_receipt
    repeated = import_clinical_package(updated)
    assert repeated.unchanged
    assert repeated.applied_migrations == applied.applied_migrations
    assert not plan_clinical_reference_import(clinical_snapshot(baseline)).can_import


def test_reviewed_terminology_change_requires_exact_persisted_value() -> None:
    baseline = package()
    baseline.finding["lesion"].description = "Original wording."
    import_clinical_package(baseline)
    updated = package(version="2.0.0")
    updated.finding["lesion"].description = "Reviewed wording."
    snapshot = clinical_snapshot(updated)
    assert not plan_clinical_reference_import(snapshot).can_import

    rule = ReferenceMigrationRule(
        kind=ReferenceKind.FINDING,
        identity=CatalogIdentity(name="lesion"),
        field="description",
        existing="Original wording.",
        proposed="Reviewed wording.",
        reason="Reviewed spelling correction.",
    )
    rules = ReferenceMigrationCatalog(
        schema_version=1,
        targets=[
            ReferenceMigrationTarget(
                module=updated.config.name, version=updated.config.version, rules=[rule]
            )
        ],
    )
    with patch(
        "endoreg_db.services.reference_data.clinical_projection.load_reference_migrations",
        return_value=rules,
    ):
        Finding.objects.filter(name="lesion").update(description="Local wording.")
        assert not plan_clinical_reference_import(snapshot).can_import
        Finding.objects.filter(name="lesion").update(description=rule.existing)
        (migration,) = import_clinical_package(updated).applied_migrations
        assert migration.changes[0].reason == rule.reason
    assert Finding.objects.get(name="lesion").description == rule.proposed
