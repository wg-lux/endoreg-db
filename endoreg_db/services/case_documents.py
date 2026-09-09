from __future__ import annotations

from typing import Protocol, cast

from django.db import transaction

from endoreg_db.models.administration.case.case import Case
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.schemas.case_documents import (
    CaseDocumentAttachmentPayload,
    CaseDocumentMediaType,
)


class CaseDocumentNotFound(LookupError):
    """Raised when a requested case-owned resource is not visible."""


class CaseDocumentConflict(ValueError):
    """Raised when attachment would rewrite existing clinical ownership."""


class _AttachableMedia(Protocol):
    pk: int
    patient_id: int | None
    examination_id: int | None
    center_id: int | None
    sensitive_meta_id: int | None

    def save(self, *args: object, **kwargs: object) -> None: ...


def _locked_media(
    payload: CaseDocumentAttachmentPayload,
) -> RawPdfFile | VideoFile:
    if payload.media_type is CaseDocumentMediaType.PDF:
        media = (
            RawPdfFile.objects.select_for_update()
            .select_related("sensitive_meta")
            .filter(pk=payload.media_id)
            .first()
        )
    else:
        media = (
            VideoFile.objects.select_for_update()
            .select_related("sensitive_meta")
            .filter(pk=payload.media_id)
            .first()
        )
    if media is None:
        raise CaseDocumentNotFound("Document not found.")
    return media


def _assert_media_patient(
    *, media: RawPdfFile | VideoFile, patient_id: int, patient_center_id: int | None
) -> None:
    media_ref = cast(_AttachableMedia, media)
    if media_ref.patient_id is not None and media_ref.patient_id != patient_id:
        raise CaseDocumentNotFound("Document not found.")
    if (
        patient_center_id is not None
        and media_ref.center_id is not None
        and media_ref.center_id != patient_center_id
    ):
        raise CaseDocumentNotFound("Document not found.")

    sensitive_meta = media.sensitive_meta
    pseudo_patient_id = (
        sensitive_meta.pseudo_patient_id if sensitive_meta is not None else None
    )
    if pseudo_patient_id is not None and pseudo_patient_id != patient_id:
        raise CaseDocumentNotFound("Document not found.")


@transaction.atomic
def attach_document_to_case(
    *,
    case_pk: int,
    payload: CaseDocumentAttachmentPayload,
) -> Case:
    patient_case = (
        Case.objects.select_for_update()
        .select_related("patient")
        .filter(pk=case_pk)
        .first()
    )
    if patient_case is None:
        raise CaseDocumentNotFound("Case not found.")
    if patient_case.is_closed or not patient_case.is_active:
        raise CaseDocumentConflict(
            "Documents cannot be attached to a closed or inactive case."
        )
    case_patient_id = cast(int, patient_case.patient.pk)

    patient_examination = (
        PatientExamination.objects.select_for_update()
        .filter(
            pk=payload.patient_examination_id,
            patient_id=case_patient_id,
            cases__pk=patient_case.pk,
        )
        .first()
    )
    if patient_examination is None:
        raise CaseDocumentNotFound("Patient examination not found.")

    media = _locked_media(payload)
    _assert_media_patient(
        media=media,
        patient_id=case_patient_id,
        patient_center_id=patient_case.patient.center_id,
    )
    media_ref = cast(_AttachableMedia, media)
    if media_ref.examination_id == patient_examination.pk:
        if media_ref.patient_id is None:
            media.patient = patient_case.patient
            media.save(update_fields=["patient", "date_modified"])
        return patient_case
    if media_ref.examination_id is not None:
        raise CaseDocumentConflict(
            "Document is already linked to another patient examination."
        )

    media.examination = patient_examination
    update_fields = ["examination", "date_modified"]
    if media_ref.patient_id is None:
        media.patient = patient_case.patient
        update_fields.append("patient")
    media.save(update_fields=update_fields)
    return patient_case


__all__ = [
    "CaseDocumentConflict",
    "CaseDocumentNotFound",
    "attach_document_to_case",
]
