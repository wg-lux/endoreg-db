"""Study snapshots retain typed values and refuse unauthorized or invalid capture."""

from uuid import uuid4
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)
from lx_dtypes.models.knowledge_base.examination import Examination as DtypesExamination
from lx_dtypes.packages import create_package, load_package
from pydantic import ValidationError as SchemaError

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.services.reference_data.clinical_projection import (
    import_clinical_package,
)
from endoreg_db.models.other.study import Study
from endoreg_db.models.other.study_cohort import StudyCohort
from endoreg_db.models.other.study_collection_entry import StudyCollectionEntry
from endoreg_db.services.studies.collections import collect_study_examination

pytestmark = pytest.mark.django_db


@pytest.fixture
def context(
    tmp_path: Path,
) -> tuple[
    User,
    Study,
    PatientExamination,
    ClinicalReferenceImport,
    DtypesRecordPersistencePayload,
]:
    owner = User.objects.create_user(username="collection-owner")
    study = Study.objects.create(name="Synthetic study", created_by=owner)
    patient = Patient.objects.create(
        first_name="Synthetic",
        last_name="Collection",
        patient_hash="collection-test",
        is_real_person=False,
    )
    kb = load_package(
        create_package(
            tmp_path / "package",
            name="collection_test",
            version="1.0.0",
            records=[
                DtypesExamination(
                    name="inspection", name_de="Inspektion", name_en="Inspection"
                )
            ],
        )
    )
    record = DtypesRecordPersistencePayload(
        patient=str(patient.pk),
        examination="inspection",
        knowledge_base_module="collection_test",
        knowledge_base_version="1.0.0",
    )
    import_clinical_package(kb)
    examination = PatientExamination.objects.create(
        patient=patient,
        examination=Examination.objects.get(name="inspection"),
        hash="collection-examination",
    )
    package_import = ClinicalReferenceImport.objects.get(
        module="collection_test", version="1.0.0"
    )
    return owner, study, examination, package_import, record


def test_collection_roundtrip_and_immutable_amendments(
    context: tuple[
        User,
        Study,
        PatientExamination,
        ClinicalReferenceImport,
        DtypesRecordPersistencePayload,
    ],
) -> None:
    owner, study, examination, kb, record = context
    cohort = StudyCohort.objects.create(
        study=study,
        created_by=owner,
        name="Collection",
        hypothesis="Synthetic hypothesis",
    )
    first = collect_study_examination(
        owner=owner,
        study=study,
        cohort=cohort,
        cohort_examination_scope=PatientExamination.objects.all(),
        examination_scope=PatientExamination.objects.all(),
        patient_examination_id=examination.pk,
        package_import=kb,
        record=record,
    )
    first.refresh_from_db()
    captured = first.validated_record()
    assert str(captured.record.uuid) == str(record.uuid)
    assert captured.evaluation.ok
    assert captured.cohort_definition is not None
    assert captured.cohort_definition.study_id == study.pk
    assert captured.patient_examination_uuid == examination.uuid
    study.name = "Amended study"
    study.save()
    assert first.validated_record().study_definition.name == "Synthetic study"
    with pytest.raises(ValidationError, match="immutable"):
        first.save()
    replacement = StudyCollectionEntry(
        pk=first.pk,
        uuid=first.uuid,
        study=study,
        cohort=cohort,
        patient_examination=examination,
        package_import=kb,
        payload=first.payload,
    )
    with pytest.raises(ValidationError, match="immutable"):
        replacement.save()
    second = collect_study_examination(
        owner=owner,
        study=study,
        examination_scope=PatientExamination.objects.all(),
        patient_examination_id=examination.pk,
        package_import=kb,
        record=record,
    )
    assert second.pk != first.pk
    assert second.validated_record().study_definition.name == "Amended study"
    examination.refresh_from_db()
    assert examination.dtypes_record == {}


@pytest.mark.parametrize(
    "failure", ["owner", "scope", "patient", "terminology", "template", "cohort"]
)
def test_collection_rejects_invalid_capture(
    context: tuple[
        User,
        Study,
        PatientExamination,
        ClinicalReferenceImport,
        DtypesRecordPersistencePayload,
    ],
    failure: str,
) -> None:
    owner, study, examination, kb, record = context
    scope = PatientExamination.objects.all()
    cohort = None
    template = None
    if failure == "owner":
        owner = User.objects.create_user(username="other-owner")
    elif failure == "scope":
        scope = scope.none()
    elif failure == "patient":
        record.patient = "another-patient"
    elif failure == "terminology":
        record.knowledge_base_version = "2.0.0"
    elif failure == "template":
        template = "unknown-template"
    else:
        other = Study.objects.create(name="Other study", created_by=owner)
        cohort = StudyCohort.objects.create(
            study=other,
            created_by=owner,
            name="Other cohort",
            hypothesis="Other hypothesis",
        )
    with pytest.raises(
        (
            PermissionDenied,
            PatientExamination.DoesNotExist,
            ValidationError,
            SchemaError,
        )
    ):
        collect_study_examination(
            owner=owner,
            study=study,
            cohort=cohort,
            cohort_examination_scope=scope if cohort is not None else None,
            examination_scope=scope,
            patient_examination_id=examination.pk,
            package_import=kb,
            record=record,
            report_template_name=template,
        )
    assert not StudyCollectionEntry.objects.exists()


@pytest.mark.parametrize("field", ["patient_examination_uuid", "package_snapshot_id"])
def test_persisted_host_identity_tampering_is_rejected(
    context: tuple[
        User,
        Study,
        PatientExamination,
        ClinicalReferenceImport,
        DtypesRecordPersistencePayload,
    ],
    field: str,
) -> None:
    owner, study, examination, kb, record = context
    entry = collect_study_examination(
        owner=owner,
        study=study,
        examination_scope=PatientExamination.objects.all(),
        patient_examination_id=examination.pk,
        package_import=kb,
        record=record,
    )
    entry.payload[field] = (
        str(uuid4()) if field == "patient_examination_uuid" else "sha256:" + "0" * 64
    )
    with pytest.raises(ValidationError, match="mismatch"):
        entry.validated_record()


def test_command_collects_stdin_and_rejects_unscoped_owner(
    context: tuple[
        User,
        Study,
        PatientExamination,
        ClinicalReferenceImport,
        DtypesRecordPersistencePayload,
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from endoreg_db.management.commands import collect_study_examination as command

    owner, study, examination, _package_import, record = context

    output = io.StringIO()
    monkeypatch.setattr(command.sys, "stdin", io.StringIO(record.model_dump_json()))
    with pytest.raises(CommandError, match="authorization"):
        call_command(
            "collect_study_examination",
            owner=owner.pk,
            study=study.pk,
            patient_examination=examination.pk,
            module="collection_test",
            module_version="1.0.0",
            stdout=output,
        )
    assert not StudyCollectionEntry.objects.exists()
    owner.is_staff = True
    owner.save()
    monkeypatch.setattr(command.sys, "stdin", io.StringIO(record.model_dump_json()))
    call_command(
        "collect_study_examination",
        owner=owner.pk,
        study=study.pk,
        patient_examination=examination.pk,
        module="collection_test",
        module_version="1.0.0",
        stdout=output,
    )
    entry = StudyCollectionEntry.objects.get()
    assert output.getvalue().strip() == str(entry.uuid)


@pytest.mark.parametrize("missing_scope", [True, False])
def test_cohort_requires_host_resolved_membership(
    context: tuple[
        User,
        Study,
        PatientExamination,
        ClinicalReferenceImport,
        DtypesRecordPersistencePayload,
    ],
    missing_scope: bool,
) -> None:
    owner, study, examination, package_import, record = context
    cohort = StudyCohort.objects.create(
        study=study,
        created_by=owner,
        name="Restricted",
        hypothesis="Synthetic restriction",
    )
    with pytest.raises((PermissionDenied, PatientExamination.DoesNotExist)):
        collect_study_examination(
            owner=owner,
            study=study,
            cohort=cohort,
            cohort_examination_scope=None
            if missing_scope
            else PatientExamination.objects.none(),
            examination_scope=PatientExamination.objects.all(),
            patient_examination_id=examination.pk,
            package_import=package_import,
            record=record,
        )
    assert not StudyCollectionEntry.objects.exists()
