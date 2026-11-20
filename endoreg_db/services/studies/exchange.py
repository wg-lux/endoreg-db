"""One authorized persistence boundary for study workbooks and hub evidence."""

from uuid import UUID

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import QuerySet
from lx_dtypes.language import DEFAULT_LANGUAGE
from lx_dtypes.models.contracts.cohort_definition import CohortDefinitionFields
from lx_dtypes.models.contracts.package_snapshot import PackageSnapshot
from lx_dtypes.models.contracts.study import StudyDefinition
from lx_dtypes.models.contracts.study_collection import StudyCollectionRecord
from lx_dtypes.models.contracts.study_exchange import (
    StudyExchangeBundle,
    StudyExchangeCohort,
    StudyExchangePatientLink,
    StudyExchangeStudy,
)
from lx_dtypes.study_exchange import parse_study_exchange

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.models.other.study import Study
from endoreg_db.models.other.study_cohort import StudyCohort
from endoreg_db.models.other.study_collection_entry import StudyCollectionEntry
from endoreg_db.schemas.clinical_reference import ClinicalReferenceReceipt


def _study_definition(study: Study) -> StudyDefinition:
    return StudyDefinition(
        name=study.name, hypothesis=study.hypothesis, description=study.description
    )


def _cohort_definition(cohort: StudyCohort) -> CohortDefinitionFields:
    return CohortDefinitionFields.model_validate(
        {
            "name": cohort.name,
            "hypothesis": cohort.hypothesis,
            "filters": cohort.filters,
            "study_metadata": cohort.study_metadata,
        }
    )


def _snapshot(receipt: ClinicalReferenceImport) -> PackageSnapshot:
    snapshot = ClinicalReferenceReceipt.model_validate(receipt.payload).package_snapshot
    if snapshot is None:
        raise ValueError(
            "Study exchange requires a complete installed package snapshot"
        )
    return snapshot


@transaction.atomic
def export_study_exchange(
    *,
    owner: User,
    study: Study,
    examination_scope: QuerySet[PatientExamination],
) -> StudyExchangeBundle:
    """Export stored observations within the caller's current examination access."""
    study = Study.objects.select_for_update().get(pk=study.pk)
    if not owner.is_active or study.created_by_id != owner.pk:
        raise PermissionDenied("Study belongs to another owner")
    if (
        StudyCollectionEntry.objects.filter(study=study)
        .exclude(patient_examination__in=examination_scope)
        .exists()
    ):
        raise PermissionDenied(
            "Complete study export requires access to every stored examination"
        )
    cohorts = list(
        StudyCohort.objects.filter(study=study, created_by=owner).order_by(
            "definition_uuid"
        )
    )
    portable_cohorts = [
        StudyExchangeCohort(
            cohort_uuid=cohort.definition_uuid,
            study_uuid=study.uuid,
            definition=_cohort_definition(cohort),
        )
        for cohort in cohorts
    ]
    packages: dict[str, PackageSnapshot] = {}
    patients: dict[UUID, StudyExchangePatientLink] = {}
    collections: list[StudyCollectionRecord] = []
    for entry in (
        StudyCollectionEntry.objects.filter(
            study=study,
            patient_examination__in=examination_scope,
        )
        .select_related(
            "study", "cohort", "package_import", "patient_examination__patient"
        )
        .order_by("uuid")
    ):
        record = entry.validated_record().model_copy(deep=True)
        examination = entry.patient_examination
        record.record.patient = str(examination.patient.uuid)
        if record.cohort_definition is not None:
            record.cohort_definition.study_id = None
            record.cohort_definition.dataset_ids = []
        collections.append(record)
        snapshot = _snapshot(entry.package_import)
        packages[snapshot.snapshot_id] = snapshot
        patients[examination.uuid] = StudyExchangePatientLink(
            patient_examination_uuid=examination.uuid,
            patient_uuid=examination.patient.uuid,
        )
    metadata = [cohort.definition.study_metadata for cohort in portable_cohorts]
    metadata.extend(
        item.cohort_definition.study_metadata
        for item in collections
        if item.cohort_definition is not None
    )
    for definition in metadata:
        for requirement in definition.terminology_requirements:
            snapshot = _snapshot(
                ClinicalReferenceImport.objects.get(
                    module=requirement.module, version=requirement.version
                )
            )
            packages[snapshot.snapshot_id] = snapshot
    return parse_study_exchange(
        StudyExchangeBundle(
            studies=[
                StudyExchangeStudy(
                    study_uuid=study.uuid, definition=_study_definition(study)
                )
            ],
            cohorts=portable_cohorts,
            collections=collections,
            packages=list(packages.values()),
            patients=list(patients.values()),
        )
    )


@transaction.atomic
def import_study_exchange(
    *,
    owner: User,
    bundle: StudyExchangeBundle,
    examination_scope: QuerySet[PatientExamination],
) -> list[StudyCollectionEntry]:
    """Accept evidence explicitly as a local owner, without granting source access.

    Patient identities and exact package receipts must already exist locally.
    Current definitions conflict rather than overwrite; repeated identical imports
    are no-ops. Historical evaluations are verified by the shared bundle contract.
    Imported cohorts deliberately receive no live dataset or examination links.
    """
    if not owner.is_active or owner.pk is None:
        raise PermissionDenied("An active local owner is required")
    bundle = parse_study_exchange(bundle)
    examinations = {
        item.uuid: item
        for item in PatientExamination.objects.filter(
            pk__in=examination_scope.values("pk")
        )
        .select_for_update(of=("self",))
        .select_related("patient")
        .filter(
            uuid__in=[item.patient_examination_uuid for item in bundle.patients],
        )
    }
    for link in bundle.patients:
        examination = examinations.get(link.patient_examination_uuid)
        if examination is None or examination.patient.uuid != link.patient_uuid:
            raise PermissionDenied(
                "Study exchange examination identity is unavailable in the authorized scope"
            )
    receipts: dict[str, ClinicalReferenceImport] = {}
    for snapshot in bundle.packages:
        receipt = ClinicalReferenceImport.objects.select_for_update().get(
            module=snapshot.identity.knowledge_base_module,
            version=snapshot.identity.knowledge_base_version,
        )
        if _snapshot(receipt).snapshot_id != snapshot.snapshot_id:
            raise ValueError("Installed package differs from study exchange snapshot")
        receipts[snapshot.snapshot_id] = receipt
    studies: dict[UUID, Study] = {}
    for item in bundle.studies:
        study, created = Study.objects.select_for_update().get_or_create(
            uuid=item.study_uuid,
            defaults={"created_by": owner, **item.definition.model_dump()},
        )
        if study.created_by_id != owner.pk:
            raise PermissionDenied("Study identity belongs to another owner")
        if not created and _study_definition(study) != item.definition:
            raise ValueError("Study identity has a conflicting current definition")
        studies[item.study_uuid] = study
    cohorts: dict[UUID, StudyCohort] = {}
    for item in bundle.cohorts:
        cohort, created = StudyCohort.objects.select_for_update().get_or_create(
            definition_uuid=item.cohort_uuid,
            defaults={
                "created_by": owner,
                "study": studies[item.study_uuid],
                **item.definition.model_dump(mode="json"),
            },
        )
        if (
            cohort.created_by_id != owner.pk
            or cohort.study_id != studies[item.study_uuid].pk
        ):
            raise PermissionDenied("Cohort identity belongs to another owner or study")
        if not created and _cohort_definition(cohort) != item.definition:
            raise ValueError("Cohort identity has a conflicting current definition")
        cohorts[item.cohort_uuid] = cohort
    entries: list[StudyCollectionEntry] = []
    for item in bundle.collections:
        examination = examinations[item.patient_examination_uuid]
        record = item.model_copy(deep=True)
        record.record.patient = str(examination.patient_id)
        if record.cohort_definition is not None:
            record.cohort_definition.study_id = studies[item.study_uuid].pk
        payload = record.model_dump(mode="json")
        entry, created = StudyCollectionEntry.objects.get_or_create(
            uuid=item.record_uuid,
            defaults={
                "study": studies[item.study_uuid],
                "cohort": cohorts[item.cohort_uuid] if item.cohort_uuid else None,
                "patient_examination": examination,
                "package_import": receipts[item.package_snapshot_id],
                "payload": payload,
            },
        )
        if not created:
            existing = entry.validated_record().model_copy(deep=True)
            if existing.cohort_definition is not None:
                existing.cohort_definition.dataset_ids = []
            if existing != record:
                raise ValueError(
                    "Collection identity has conflicting historical evidence"
                )
        entries.append(entry)
    return entries


@transaction.atomic
def capture_study_examinations(
    *,
    owner: User,
    study: Study | None = None,
    examination_scope: QuerySet[PatientExamination],
    cohort: StudyCohort | None = None,
    cohort_examination_scope: QuerySet[PatientExamination] | None = None,
) -> list[StudyCollectionEntry]:
    """Explicitly capture current dataset ledgers; unchanged evidence is replayed.

    Callers supply the complete authorized selection and, for a cohort, its
    resolved membership. Missing ledgers or installed packages fail atomically.
    This is an authoring action; exports never invoke it implicitly.
    """
    import json

    from lx_dtypes.models.contracts.dtypes_record_persistence import (
        DtypesRecordPersistencePayload,
    )
    from endoreg_db.services.studies.collections import collect_study_examination

    if not owner.is_active:
        raise PermissionDenied("An active local owner is required")
    if cohort is not None:
        cohort = StudyCohort.objects.select_for_update().get(pk=cohort.pk)
        if cohort.created_by_id != owner.pk:
            raise PermissionDenied("Cohort belongs to another owner")
        if cohort_examination_scope is None:
            raise PermissionDenied("Capture requires resolved cohort membership")
        if examination_scope.exclude(
            pk__in=cohort_examination_scope.values("pk")
        ).exists():
            raise PermissionDenied("Capture selection exceeds cohort membership")
        if study is None:
            study = cohort.study
            if study is None:
                study = Study.objects.create(
                    name=cohort.name, hypothesis=cohort.hypothesis, created_by=owner
                )
                cohort.study = study
                cohort.save()
        elif cohort.study_id != study.pk:
            raise PermissionDenied("Cohort belongs to another study")
    elif cohort_examination_scope is not None:
        raise ValueError("Cohort membership scope requires a cohort")
    if study is None:
        raise ValueError("Capture requires a study or an owned cohort")
    study = Study.objects.select_for_update().get(pk=study.pk)
    if study.created_by_id != owner.pk:
        raise PermissionDenied("Study belongs to another owner")
    prepared: list[
        tuple[
            PatientExamination,
            DtypesRecordPersistencePayload,
            ClinicalReferenceImport,
            str,
        ]
    ] = []
    # Authorized cohort queries may be DISTINCT; lock base rows rather than
    # applying PostgreSQL FOR UPDATE to a distinct/joined query.
    examination_scope = PatientExamination.objects.filter(
        pk__in=examination_scope.values("pk")
    )
    for examination in examination_scope.select_for_update(of=("self",)).order_by("pk"):
        if not examination.dtypes_record:
            raise ValueError(
                "Every selected examination requires a saved structured ledger before capture"
            )
        record = DtypesRecordPersistencePayload.model_validate_json(
            json.dumps(examination.dtypes_record)
        )
        try:
            receipt = ClinicalReferenceImport.objects.get(
                module=record.knowledge_base_module,
                version=record.knowledge_base_version,
            )
        except ClinicalReferenceImport.DoesNotExist as error:
            raise ValueError(
                "Install the exact ledger terminology package before capturing study evidence"
            ) from error
        prepared.append((examination, record, receipt, _snapshot(receipt).snapshot_id))
    result: list[StudyCollectionEntry] = []
    for examination, record, receipt, snapshot_id in prepared:
        replay = None
        for entry in (
            StudyCollectionEntry.objects.filter(
                study=study, cohort=cohort, patient_examination=examination
            )
            .select_related("study", "cohort", "patient_examination", "package_import")
            .order_by("-created_at", "-pk")
        ):
            prior = entry.validated_record()
            cohort_matches = (
                prior.cohort_definition is None
                if cohort is None
                else (
                    prior.cohort_definition is not None
                    and CohortDefinitionFields.model_validate(
                        prior.cohort_definition.model_dump(
                            exclude={"study_id", "dataset_ids"}
                        )
                    )
                    == _cohort_definition(cohort)
                    and sorted(prior.cohort_definition.dataset_ids)
                    == sorted(cohort.datasets.values_list("pk", flat=True))
                )
            )
            if (
                prior.record == record
                and prior.study_definition == _study_definition(study)
                and prior.package_snapshot_id == snapshot_id
                and prior.report_template_name is None
                and prior.language == DEFAULT_LANGUAGE
                and cohort_matches
            ):
                replay = entry
                break
        result.append(
            replay
            if replay is not None
            else collect_study_examination(
                owner=owner,
                study=study,
                examination_scope=examination_scope,
                patient_examination_id=examination.pk,
                package_import=receipt,
                record=record,
                cohort=cohort,
                cohort_examination_scope=cohort_examination_scope,
            )
        )
    return result
