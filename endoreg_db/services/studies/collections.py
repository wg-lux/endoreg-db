"""Capture authorized study observations with server-computed KB evaluations."""

import json
from collections.abc import Iterator
from typing import cast
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import (
    Case,
    F,
    IntegerField,
    JSONField,
    OuterRef,
    Prefetch,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from lx_dtypes.language import DEFAULT_LANGUAGE, LanguageCode
from lx_dtypes.models.contracts.dtypes_record_persistence import (
    DtypesRecordPersistencePayload,
)
from lx_dtypes.models.contracts.application_settings import (
    AnnotationAcademicTitle,
    AnnotationProfession,
)
from lx_dtypes.models.contracts.study_collection import (
    StudyCollectionRecord,
    StudyReportSource,
)
from lx_dtypes.models.contracts.study_metadata import StudyMetadata
from lx_dtypes.models.contracts.study import StudyDefinition
from lx_dtypes.models.contracts.cohort_definition import CohortDefinition
from lx_dtypes.evaluation import assess_examination

from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.other.clinical_reference_import import ClinicalReferenceImport
from endoreg_db.services.reference_data.clinical_projection import (
    require_clinical_package,
)
from endoreg_db.services.reference_data.clinical_projection import load_imported_package
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.reports.persistence import authored_final_reports
from endoreg_db.models.other.study import Study
from endoreg_db.models.other.study_cohort import StudyCohort
from endoreg_db.models.other.study_collection_entry import StudyCollectionEntry


def _ranked_reports(
    preferred: AnnotationProfession,
) -> QuerySet[PatientExaminationReport]:
    return authored_final_reports().order_by(
        Case(
            When(author_profession=preferred.value, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        "-updated_at",
        "-pk",
    )


def with_study_record(
    examinations: QuerySet[PatientExamination], preferred: AnnotationProfession
) -> QuerySet[PatientExamination]:
    """Expose the same selected snapshot to SQL finding filters before preview pagination."""
    candidate = _ranked_reports(preferred).filter(patient_examination_id=OuterRef("pk"))
    return examinations.alias(
        study_source_record=Coalesce(
            Subquery(candidate.values("dtypes_record")[:1]),
            F("dtypes_record"),
            output_field=JSONField(),
        )
    )


def study_record_sources(
    examinations: QuerySet[PatientExamination], metadata: StudyMetadata
) -> Iterator[
    tuple[
        PatientExamination,
        DtypesRecordPersistencePayload | None,
        StudyReportSource | None,
    ]
]:
    """Resolve one structured record for each authorized examination in bounded batches."""
    preferred = metadata.preferred_report_profession
    if preferred is not None:
        candidates = (
            _ranked_reports(preferred)
            .only(
                "patient_examination_id",
                "uuid",
                "version",
                "author_profession",
                "author_academic_titles",
                "dtypes_record",
            )
            .prefetch_related(
                Prefetch(
                    "source_documents",
                    queryset=RawPdfFile.objects.only("uuid").order_by("uuid"),
                )
            )
        )
        examinations = examinations.prefetch_related(
            Prefetch(
                "reports", queryset=candidates[:1], to_attr="selected_study_reports"
            )
        )
    for examination in examinations.order_by("pk").iterator(chunk_size=200):
        source = None
        data = examination.dtypes_record
        if preferred is not None:
            reports = cast(
                list[PatientExaminationReport],
                getattr(examination, "selected_study_reports"),
            )
            if reports:
                report = reports[0]
                data = report.dtypes_record
                source = StudyReportSource(
                    report_uuid=report.uuid,
                    report_version=report.version,
                    author_academic_titles=[
                        AnnotationAcademicTitle(title)
                        for title in report.author_academic_titles
                    ],
                    source_document_uuids=[
                        doc.uuid for doc in report.source_documents.all()
                    ],
                    author_profession=AnnotationProfession(report.author_profession)
                    if report.author_profession
                    else None,
                )
        record = (
            DtypesRecordPersistencePayload.model_validate_json(json.dumps(data))
            if source is not None or data
            else None
        )
        yield examination, record, source


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
    report_source: StudyReportSource | None = None,
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
    package_import, snapshot = require_clinical_package(
        package_import.module, package_import.version
    )
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
        report_source=report_source,
        package_snapshot_id=snapshot.snapshot_id,
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
