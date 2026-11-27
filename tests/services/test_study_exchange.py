"""Stored evidence has one portable, authorized and replay-safe boundary."""

from pathlib import Path
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)
from lx_dtypes.models.knowledge_base.examination import Examination as DtypesExamination
from lx_dtypes.packages import create_package, load_package

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.models.other.study import Study
from endoreg_db.models.other.study_cohort import StudyCohort
from endoreg_db.models.other.study_collection_entry import StudyCollectionEntry
from endoreg_db.services.reference_data.clinical_projection import (
    import_clinical_package,
)
from endoreg_db.services.studies.collections import collect_study_examination
from endoreg_db.services.studies.exchange import (
    export_study_exchange,
    import_study_exchange,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[User, Study, PatientExamination]:
    owner = User.objects.create_user(username="exchange-owner")
    study = Study.objects.create(name="Synthetic exchange", created_by=owner)
    patient = Patient.objects.create(
        first_name="Synthetic",
        last_name="Exchange",
        patient_hash="exchange-test",
        is_real_person=False,
    )
    kb = load_package(
        create_package(
            tmp_path / "package",
            name="exchange_test",
            version="1.0.0",
            records=[
                DtypesExamination(
                    name="inspection", name_de="Inspektion", name_en="Inspection"
                )
            ],
        )
    )
    import_clinical_package(kb)
    examination = PatientExamination.objects.create(
        patient=patient,
        examination=Examination.objects.get(name="inspection"),
        hash="exchange-examination",
    )
    cohort = StudyCohort.objects.create(
        study=study,
        created_by=owner,
        name="Synthetic cohort",
        hypothesis="Synthetic hypothesis",
    )
    collect_study_examination(
        owner=owner,
        study=study,
        cohort=cohort,
        cohort_examination_scope=PatientExamination.objects.all(),
        examination_scope=PatientExamination.objects.all(),
        patient_examination_id=examination.pk,
        package_import=ClinicalReferenceImport.objects.get(module="exchange_test"),
        record=DtypesRecordPersistencePayload(
            patient=str(patient.pk),
            examination="inspection",
            knowledge_base_module="exchange_test",
            knowledge_base_version="1.0.0",
        ),
    )
    return owner, study, examination


def test_portable_roundtrip_and_repeat_import(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, examination = evidence
    scope = PatientExamination.objects.all().distinct()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    assert bundle.collections[0].record.patient == str(examination.patient.uuid)
    definition = bundle.collections[0].cohort_definition
    assert (
        definition is not None
        and definition.study_id is None
        and not definition.dataset_ids
    )
    assert bundle.patients[0].patient_uuid == examination.patient.uuid
    assert (
        import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)[
            0
        ].uuid
        == bundle.collections[0].record_uuid
    )
    assert (
        import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)[
            0
        ].uuid
        == bundle.collections[0].record_uuid
    )
    assert StudyCollectionEntry.objects.count() == 1


def test_scope_revocation_and_foreign_owner(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, _ = evidence
    scope = PatientExamination.objects.all()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    with pytest.raises(PermissionDenied, match="Complete study"):
        export_study_exchange(owner=owner, study=study, examination_scope=scope.none())
    with pytest.raises(PermissionDenied, match="scope"):
        import_study_exchange(
            owner=owner, bundle=bundle, examination_scope=scope.none()
        )
    stranger = User.objects.create_user(username="exchange-stranger")
    with pytest.raises(PermissionDenied):
        export_study_exchange(owner=stranger, study=study, examination_scope=scope)
    with pytest.raises(PermissionDenied):
        import_study_exchange(owner=stranger, bundle=bundle, examination_scope=scope)


def test_conflicts_rollback_new_definitions(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, _ = evidence
    scope = PatientExamination.objects.all()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    new_study = bundle.studies[0].model_copy(update={"study_uuid": uuid4()})
    bundle.studies.insert(0, new_study)
    bundle.collections[
        0
    ].study_definition.description = "Conflicting historical evidence"
    with pytest.raises(ValueError, match="conflicting historical"):
        import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)
    assert not Study.objects.filter(uuid=new_study.study_uuid).exists()
    assert StudyCollectionEntry.objects.count() == 1


def test_import_recreates_evidence_without_activating_cohort(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, _ = evidence
    scope = PatientExamination.objects.all()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    StudyCollectionEntry.objects.all().delete()
    study.delete()
    entries = import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)
    assert len(entries) == 1
    assert entries[0].validated_record().evaluation == bundle.collections[0].evaluation
    cohort = entries[0].cohort
    assert cohort is not None
    assert not cohort.datasets.exists() and not cohort.imported_examinations.exists()


def test_import_requires_installed_package_before_creating_studies(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, _ = evidence
    scope = PatientExamination.objects.all()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    StudyCollectionEntry.objects.all().delete()
    study.delete()
    ClinicalReferenceImport.objects.all().delete()
    from endoreg_db.services.reference_data.clinical_projection import (
        ClinicalPackageRequiredError,
    )

    with pytest.raises(ClinicalPackageRequiredError):
        import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)
    assert not Study.objects.exists()


def test_patient_uuid_must_match_authorized_existing_examination(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, _ = evidence
    scope = PatientExamination.objects.all()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    other_patient_uuid = uuid4()
    bundle.patients[0] = bundle.patients[0].model_copy(
        update={"patient_uuid": other_patient_uuid}
    )
    bundle.collections[0].record.patient = str(other_patient_uuid)
    with pytest.raises(PermissionDenied, match="identity"):
        import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)
    assert StudyCollectionEntry.objects.count() == 1


@pytest.mark.parametrize("changed_semantics", [False, True])
def test_package_identity_uses_semantic_digest(
    evidence: tuple[User, Study, PatientExamination],
    tmp_path: Path,
    changed_semantics: bool,
) -> None:
    from lx_dtypes.models.contracts.package_snapshot import build_package_snapshot

    owner, study, _ = evidence
    scope = PatientExamination.objects.all()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    rebuilt = build_package_snapshot(
        load_package(
            create_package(
                tmp_path / "independent-package",
                name="exchange_test",
                version="1.0.0",
                records=[
                    DtypesExamination(
                        name="inspection",
                        name_de="Inspektion",
                        name_en="Different inspection"
                        if changed_semantics
                        else "Inspection",
                    )
                ],
            )
        )
    )
    original = bundle.packages[0]
    assert original != rebuilt
    assert (original.snapshot_id != rebuilt.snapshot_id) is changed_semantics
    bundle.packages[0] = rebuilt
    bundle.collections[0] = bundle.collections[0].model_copy(
        update={"package_snapshot_id": rebuilt.snapshot_id}
    )
    if changed_semantics:
        with pytest.raises(ValueError, match="Installed package differs"):
            import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)
    else:
        assert (
            len(
                import_study_exchange(
                    owner=owner, bundle=bundle, examination_scope=scope
                )
            )
            == 1
        )


def test_import_remaps_persistent_identity_to_receiver_primary_keys(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    owner, study, examination = evidence
    scope = PatientExamination.objects.all()
    bundle = export_study_exchange(owner=owner, study=study, examination_scope=scope)
    patient = examination.patient
    old_patient_id, old_examination_id = patient.pk, examination.pk
    patient_uuid, examination_uuid = patient.uuid, examination.uuid
    examination_type = examination.examination_safe
    StudyCollectionEntry.objects.all().delete()
    examination.delete()
    patient.delete()
    receiver_patient = Patient.objects.create(
        pk=old_patient_id + 100,
        uuid=patient_uuid,
        first_name="Synthetic",
        last_name="Receiver",
        patient_hash="receiver-test",
        is_real_person=False,
    )
    receiver_examination = PatientExamination.objects.create(
        pk=old_examination_id + 100,
        uuid=examination_uuid,
        patient=receiver_patient,
        examination=examination_type,
        hash="receiver-examination",
    )
    entries = import_study_exchange(owner=owner, bundle=bundle, examination_scope=scope)
    assert entries[0].patient_examination.pk == receiver_examination.pk
    assert entries[0].validated_record().record.patient == str(receiver_patient.pk)
    assert (
        export_study_exchange(
            owner=owner, study=study, examination_scope=scope
        ).collections
        == bundle.collections
    )


def test_capture_current_ledger_is_explicit_and_idempotent(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    from endoreg_db.services.studies.exchange import capture_study_examinations

    owner, study, examination = evidence
    entry = StudyCollectionEntry.objects.get()
    examination.dtypes_record = entry.validated_record().record.model_dump(mode="json")
    examination.save()
    scope = PatientExamination.objects.filter(pk=examination.pk).distinct()
    for _ in range(2):
        captured = capture_study_examinations(
            owner=owner,
            study=study,
            cohort=entry.cohort,
            examination_scope=scope,
            cohort_examination_scope=scope,
        )
        assert captured[0].pk == entry.pk
    study.description = "Updated definition"
    study.save()
    captured = capture_study_examinations(
        owner=owner,
        study=study,
        cohort=entry.cohort,
        examination_scope=scope,
        cohort_examination_scope=scope,
    )
    assert captured[0].pk != entry.pk
    assert StudyCollectionEntry.objects.count() == 2


def test_capture_cohort_creates_owned_study_and_rejects_missing_ledgers(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    from endoreg_db.services.studies.exchange import capture_study_examinations

    owner, _, examination = evidence
    cohort = StudyCohort.objects.create(
        created_by=owner, name="Imported workflow", hypothesis="Synthetic protocol"
    )
    scope = PatientExamination.objects.filter(pk=examination.pk)
    before = Study.objects.count()
    with pytest.raises(ValueError, match="saved structured ledger"):
        capture_study_examinations(
            owner=owner,
            cohort=cohort,
            examination_scope=scope,
            cohort_examination_scope=scope,
        )
    cohort.refresh_from_db()
    assert cohort.study_id is None and Study.objects.count() == before
    examination.dtypes_record = (
        StudyCollectionEntry.objects.get()
        .validated_record()
        .record.model_dump(mode="json")
    )
    examination.save()
    captured = capture_study_examinations(
        owner=owner,
        cohort=cohort,
        examination_scope=scope,
        cohort_examination_scope=scope,
    )
    cohort.refresh_from_db()
    assert cohort.study_id == captured[0].study.pk
    assert captured[0].study.created_by_id == owner.pk


def test_cohorts_choose_independent_report_sources_and_retain_prior_observations(
    evidence: tuple[User, Study, PatientExamination],
) -> None:
    from lx_dtypes.study_exchange import (
        study_exchange_from_xlsx,
        study_exchange_to_xlsx,
    )
    from endoreg_db.services.studies.exchange import capture_study_examinations

    owner, study, examination = evidence
    original = StudyCollectionEntry.objects.get().validated_record().record
    examination.dtypes_record = original.model_dump(mode="json")
    examination.save()
    scope = PatientExamination.objects.filter(pk=examination.pk)
    cohorts = {
        role: StudyCohort.objects.create(
            study=study,
            created_by=owner,
            name=role,
            hypothesis="Source selection",
            study_metadata={"preferred_report_profession": role},
        )
        for role in ("physician", "medical_assistant")
    }

    def capture(role: str) -> StudyCollectionEntry:
        return capture_study_examinations(
            owner=owner,
            cohort=cohorts[role],
            examination_scope=scope,
            cohort_examination_scope=scope,
        )[0]

    assert capture("physician").validated_record().report_source is None
    reports: dict[str, PatientExaminationReport] = {}
    for role in cohorts:
        report_record = original.model_copy(update={"tags": [role]})
        reports[role] = PatientExaminationReport.objects.create(
            patient_examination=examination,
            template_name="test",
            updated_by=User.objects.create_user(username=role),
            author_profession=role,
            status="final",
            dtypes_record=report_record.model_dump(mode="json"),
        )
    # A newer draft can never displace the physician's final structured record.
    PatientExaminationReport.objects.create(
        patient_examination=examination,
        template_name="test",
        status="draft",
        author_profession="physician",
        updated_by=owner,
        dtypes_record=examination.dtypes_record,
    )
    for role, report in reports.items():
        entry = capture(role)
        saved = entry.validated_record()
        assert saved.report_source is not None
        assert saved.report_source.report_uuid == report.uuid
        assert saved.report_source.author_profession == role
        assert saved.record.tags == [role]
        assert capture(role).pk == entry.pk

    first = capture("physician")
    physician = reports["physician"]
    physician.version += 1
    physician.save()
    second = capture("physician")
    assert second.pk != first.pk
    restored = study_exchange_from_xlsx(
        study_exchange_to_xlsx(
            export_study_exchange(owner=owner, study=study, examination_scope=scope)
        )
    )
    versions = {
        row.report_source.report_version
        for row in restored.collections
        if row.report_source and row.report_source.report_uuid == physician.uuid
    }
    assert versions == {1, 2}
    physician.is_active = False
    physician.save()
    fallback = capture("physician").validated_record().report_source
    assert fallback is not None
    assert fallback.report_uuid == reports["medical_assistant"].uuid
