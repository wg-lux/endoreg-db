"""Capture authorized study observations with server-computed KB evaluations."""

from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from lx_dtypes.language import DEFAULT_LANGUAGE, LanguageCode
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)
from lx_dtypes.models.contracts.study_collection import StudyCollectionRecord
from lx_dtypes.models.contracts.study import StudyDefinition
from lx_dtypes.models.contracts.cohort_definition import CohortDefinition
from lx_dtypes.validation import assess_examination

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.schemas.clinical_reference import ClinicalReferenceReceipt
from endoreg_db.services.reference_data.clinical_projection import load_imported_package
from endoreg_db.models.other.study import Study
from endoreg_db.models.other.study_cohort import StudyCohort
from endoreg_db.models.other.study_collection_entry import StudyCollectionEntry


@transaction.atomic
def collect_study_examination(
    *,
    owner: User,
    study: Study,
    examination_scope: QuerySet[PatientExamination],
    patient_examination_id: int,
    package_import: ClinicalReferenceImport,
    record: DtypesRecordPersistencePayload,
    cohort: StudyCohort | None = None,
    cohort_examination_scope: QuerySet[PatientExamination] | None = None,
    report_template_name: str | None = None,
    language: LanguageCode = DEFAULT_LANGUAGE,
) -> StudyCollectionEntry:
    """Persist a typed snapshot; callers supply their authorized examination scope.

    Evaluation is always recomputed from the submitted record and exact KB. Each
    collection is immutable, so amendments create another entry without replacing
    historical evidence or mutating the live clinical record. A cohort requires
    an explicit scope resolved by the host from its live dataset, eligibility and
    filter rules; an authorized examination scope alone does not prove membership.
    """
    study = Study.objects.select_for_update().get(pk=study.pk)
    if study.created_by_id != owner.pk:
        raise PermissionDenied("Study belongs to another owner")
    if cohort is not None:
        if cohort_examination_scope is None:
            raise PermissionDenied(
                "Collection requires an explicit live cohort membership scope"
            )
        cohort = StudyCohort.objects.select_for_update().get(pk=cohort.pk)
        examination_scope = examination_scope.filter(
            pk__in=cohort_examination_scope.values("pk")
        )
    elif cohort_examination_scope is not None:
        raise ValueError("Cohort membership scope requires a cohort")
    examination = examination_scope.select_for_update().get(pk=patient_examination_id)
    package_import = ClinicalReferenceImport.objects.get(pk=package_import.pk)
    receipt = ClinicalReferenceReceipt.model_validate(package_import.payload)
    if receipt.package_snapshot is None:
        raise ValueError("Collection requires a complete imported package snapshot")
    knowledge_base = load_imported_package(
        package_import.module, package_import.version
    )
    record = DtypesRecordPersistencePayload.model_validate(
        record.model_dump(mode="python")
    )
    evaluation = assess_examination(
        knowledge_base,
        record.model_dump(mode="python"),
        template_name=report_template_name,
        language=language,
    )
    payload = StudyCollectionRecord(
        record_uuid=uuid4(),
        package_snapshot_id=receipt.package_snapshot.snapshot_id,
        study_uuid=study.uuid,
        cohort_uuid=cohort.definition_uuid if cohort else None,
        patient_examination_uuid=examination.uuid,
        study_definition=StudyDefinition(
            name=study.name, hypothesis=study.hypothesis, description=study.description
        ),
        cohort_definition=CohortDefinition.model_validate(
            {
                "name": cohort.name,
                "hypothesis": cohort.hypothesis,
                "filters": cohort.filters,
                "study_metadata": cohort.study_metadata,
                "study_id": study.pk,
                "dataset_ids": list(cohort.datasets.values_list("pk", flat=True)),
            }
        )
        if cohort
        else None,
        record=record,
        evaluation=evaluation,
        report_template_name=report_template_name,
        evaluated_at=timezone.now(),
        language=language,
    )
    return StudyCollectionEntry.objects.create(
        uuid=payload.record_uuid,
        package_import=package_import,
        study=study,
        cohort=cohort,
        patient_examination=examination,
        payload=payload.model_dump(mode="json"),
    )
