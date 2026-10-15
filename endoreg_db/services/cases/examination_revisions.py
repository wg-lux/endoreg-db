"""Atomic revision editing and explicit case selection over the canonical graph."""

from typing import cast
from pydantic import BaseModel

from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.examination_revision import (
    CaseExaminationSelection,
    ExaminationClinicalContent,
    ExaminationEvidence,
    ExaminationRevisionCreate,
    ExaminationRevisionSnapshot,
    ExaminationRevisionUpdate,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.ledger.medical import (
    patient_lab_value_from_endoreg,
    patient_medication_from_endoreg,
)
from lx_dtypes.models.ledger.medical.Write import (
    PatientLabValueCreate,
    PatientMedicationCreate,
)

from endoreg_db.models.administration.case.case import Case
from endoreg_db.models.medical.patient.examination_revision import (
    CaseExaminationRecord,
    ExaminationRevision,
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_lab_value import PatientLabValue
from endoreg_db.models.medical.patient.patient_medication import PatientMedication
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.other.study_table_import import StudyTableImport
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.media.export_downloads import validate_video_download


class ExaminationRevisionConflict(ValueError):
    """Stale version, incomplete evidence, or incompatible examination ownership."""


def _write_fields(record: BaseModel, contract: type[BaseModel]) -> dict[str, object]:
    return {field: getattr(record, field) for field in contract.model_fields}


def _assert_content(
    examination: PatientExamination, content: ExaminationClinicalContent
) -> None:
    clinical = content.examination
    if (
        clinical.patient != str(examination.patient_id)
        or clinical.examination != examination.examination_safe.name
    ):
        raise ExaminationRevisionConflict(
            "Clinical content belongs to another patient or examination."
        )
    if (
        clinical.knowledge_base_module or "",
        clinical.knowledge_base_version or "",
    ) != (examination.knowledge_base_module, examination.knowledge_base_version):
        raise ExaminationRevisionConflict(
            "Clinical content must retain the examination's terminology version."
        )


def _assert_version(record: ExaminationRevision, expected_version: int) -> None:
    if record.version != expected_version:
        raise ExaminationRevisionConflict(
            "The examination revision changed; reload before editing."
        )


@transaction.atomic
def capture_examination_revision(
    examination: PatientExamination,
    payload: ExaminationRevisionCreate,
) -> ExaminationRevision:
    examination = PatientExamination.objects.select_for_update().get(pk=examination.pk)
    if examination.pk != payload.patient_examination_id:
        raise ExaminationRevisionConflict(
            "Examination selection does not match its owner."
        )
    if payload.source_revision_id is not None:
        source = ExaminationRevision.objects.filter(
            pk=payload.source_revision_id, patient_examination=examination
        ).first()
        if source is None:
            raise ExaminationRevisionConflict(
                "The source revision does not belong to this examination."
            )
        return ExaminationRevision.objects.create(
            patient_examination=examination,
            title=payload.title,
            snapshot=source.snapshot,
        )
    selection = payload.selection
    clinical = examination.dtypes_record
    evidence = ExaminationEvidence(
        medication_ids=selection.medication_ids,
        lab_value_ids=selection.lab_value_ids,
        table_import_ids=list(
            StudyTableImport.objects.filter(patient_examination=examination)
            .order_by("pk")
            .values_list("pk", flat=True)
        ),
    )
    if selection.report_id is not None:
        report = PatientExaminationReport.objects.filter(
            pk=selection.report_id,
            patient_examination=examination,
            is_active=True,
        ).first()
        if report is None or report.dtypes_record is None:
            raise ExaminationRevisionConflict(
                "Selected report has no examination-owned clinical snapshot."
            )
        clinical = report.dtypes_record
        evidence.report_id = int(report.pk)
        evidence.report_version = report.version
        evidence.report_text = report.rendered_text
        evidence.report_template_name = report.template_name
        evidence.report_template_version = report.template_version
        evidence.report_template_hash = report.template_hash
    if not clinical:
        raise ExaminationRevisionConflict(
            "Save the complete clinical examination before capturing a revision."
        )
    if selection.video_id is not None:
        video = (
            VideoFile.objects.filter(pk=selection.video_id, examination=examination)
            .select_related("state")
            .first()
        )
        if video is None:
            raise ExaminationRevisionConflict(
                "Selected video does not belong to the examination."
            )
        evidence.video_id = int(video.pk)
        evidence.processed_sha256 = video.processed_video_hash or None
        if selection.annotator is not None:
            validate_video_download(video)
            from endoreg_db.export.frames.export_frames_with_labels import (
                video_annotation_download_rows,
            )
            from lx_dtypes.models.contracts.examination_revision import (
                ExaminationAnnotationSnapshot,
            )

            evidence.annotations = [
                ExaminationAnnotationSnapshot.model_validate(
                    {
                        key: row[key]
                        for key in ExaminationAnnotationSnapshot.model_fields
                    }
                )
                for row in video_annotation_download_rows(video, use_export_flags=False)
                if row["annotator"] == selection.annotator
            ]
            if not evidence.annotations:
                raise ExaminationRevisionConflict(
                    "The selected annotation track has no exportable annotations."
                )
    medications = list(
        PatientMedication.objects.filter(
            patient_id=examination.patient_id, pk__in=selection.medication_ids
        ).order_by("pk")
    )
    values = list(
        PatientLabValue.objects.filter(
            patient_id=examination.patient_id, pk__in=selection.lab_value_ids
        ).order_by("pk")
    )
    if len(medications) != len(selection.medication_ids) or len(values) != len(
        selection.lab_value_ids
    ):
        raise ExaminationRevisionConflict(
            "Selected medication or lab value belongs to another patient or is unavailable."
        )
    content = ExaminationClinicalContent.model_validate(
        {
            "examination": clinical,
            "medications": [
                _write_fields(
                    patient_medication_from_endoreg(item), PatientMedicationCreate
                )
                for item in medications
            ],
            "lab_values": [
                _write_fields(
                    patient_lab_value_from_endoreg(item), PatientLabValueCreate
                )
                for item in values
            ],
        }
    )
    _assert_content(examination, content)
    snapshot = ExaminationRevisionSnapshot(content=content, evidence=evidence)
    return ExaminationRevision.objects.create(
        patient_examination=examination,
        title=payload.title,
        snapshot=cast(JsonObject, snapshot.model_dump(mode="json")),
    )


@transaction.atomic
def update_examination_revision(
    record: ExaminationRevision, payload: ExaminationRevisionUpdate
) -> ExaminationRevision:
    record = ExaminationRevision.objects.select_for_update().get(pk=record.pk)
    _assert_version(record, payload.expected_version)
    if record.finalized_at is not None:
        raise ExaminationRevisionConflict(
            "Finalized revisions cannot be edited; create an alternative."
        )
    _assert_content(record.patient_examination, payload.content)
    snapshot = ExaminationRevisionSnapshot.model_validate(record.snapshot)
    snapshot.content = payload.content
    record.snapshot = cast(JsonObject, snapshot.model_dump(mode="json"))
    record.title = payload.title
    record.version += 1
    record.save()
    return record


@transaction.atomic
def delete_examination_revision(
    record: ExaminationRevision, expected_version: int
) -> None:
    record = ExaminationRevision.objects.select_for_update().get(pk=record.pk)
    _assert_version(record, expected_version)
    if record.finalized_at is not None:
        raise ExaminationRevisionConflict(
            "Finalized revisions are retained; only drafts can be deleted."
        )
    record.delete()


@transaction.atomic
def finalize_examination_revision(
    record: ExaminationRevision, expected_version: int
) -> ExaminationRevision:
    record = ExaminationRevision.objects.select_for_update().get(pk=record.pk)
    _assert_version(record, expected_version)
    if record.finalized_at is not None:
        return record
    snapshot = ExaminationRevisionSnapshot.model_validate(record.snapshot)
    _assert_content(record.patient_examination, snapshot.content)
    from endoreg_db.services.interoperability.medical_ledger import (
        validate_medical_references,
    )

    validate_medical_references(
        medications=snapshot.content.medications, lab_values=snapshot.content.lab_values
    )
    clinical = snapshot.content.examination
    if clinical.knowledge_base_module and clinical.knowledge_base_version:
        from lx_dtypes.terminology.terminology_loader import load_module_kb
        from lx_dtypes.models.ledger.p_examination.Pydantic import PExamination

        knowledge_base = load_module_kb(
            clinical.knowledge_base_module, version=clinical.knowledge_base_version
        )
        knowledge_base.assert_examination_admissibility(
            PExamination.model_validate(clinical.model_dump())
        )
        if snapshot.evidence.report_template_name:
            from lx_dtypes.validation import assess_examination

            assessment = assess_examination(
                knowledge_base,
                clinical.model_dump(),
                template_name=snapshot.evidence.report_template_name,
            )
            if not assessment.ok:
                raise ExaminationRevisionConflict(
                    "The examination does not satisfy its selected report template requirements."
                )
    elif clinical.patient_findings or clinical.patient_indications:
        raise ExaminationRevisionConflict(
            "Finalizing descriptors and findings requires the recorded terminology module and version."
        )
    if snapshot.evidence.video_id is not None:
        video = VideoFile.objects.filter(
            pk=snapshot.evidence.video_id, examination=record.patient_examination
        ).first()
        if video is None:
            raise ExaminationRevisionConflict(
                "Selected video is no longer available for this examination."
            )
        validate_video_download(video)
        if video.processed_video_hash != snapshot.evidence.processed_sha256:
            raise ExaminationRevisionConflict(
                "Media generation changed; capture a new revision."
            )
    record.finalized_at = timezone.now()
    record.save(update_fields=["finalized_at", "updated_at"])
    return record


@transaction.atomic
def select_case_examination_revision(
    case: Case, payload: CaseExaminationSelection
) -> CaseExaminationRecord:
    case = Case.objects.select_for_update().get(pk=case.pk)
    if case.is_closed or case.is_deleted:
        raise ExaminationRevisionConflict(
            "Reopen the case before changing examination selections."
        )
    record = (
        ExaminationRevision.objects.select_for_update()
        .filter(pk=payload.revision_id)
        .first()
    )
    if (
        record is None
        or not case.patient_examinations.filter(
            pk=record.patient_examination.pk, patient_id=case.patient.pk
        ).exists()
    ):
        raise ExaminationRevisionConflict(
            "Revision does not belong to an examination in this case."
        )
    _assert_version(record, payload.expected_version)
    if record.finalized_at is None:
        raise ExaminationRevisionConflict(
            "Finalize the examination revision before selecting it for the case."
        )
    selected, _ = CaseExaminationRecord.objects.update_or_create(
        case=case,
        patient_examination=record.patient_examination,
        defaults={"revision": record},
    )
    return selected


def assert_case_examinations_complete(case: Case) -> None:
    examination_ids = set(case.patient_examinations.values_list("pk", flat=True))
    selected_ids = set(
        CaseExaminationRecord.objects.filter(
            case=case,
            revision__finalized_at__isnull=False,
        ).values_list("patient_examination_id", flat=True)
    )
    if not examination_ids or examination_ids != selected_ids:
        raise ExaminationRevisionConflict(
            "Select one finalized revision for every examination before closing or exporting the case."
        )
