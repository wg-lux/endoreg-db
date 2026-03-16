from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from django.db import transaction

from endoreg_db.models import Examination, PatientExamination, RawPdfFile, VideoFile
from endoreg_db.services.case_resolution_state import (
    get_case_resolution_meta,
    persist_auto_case_resolution_state,
)


@dataclass(slots=True)
class AutoCaseResolutionResult:
    status: Literal["linked", "ambiguous", "unresolved"]
    patient_examination: PatientExamination | None = None
    created: bool = False
    reason: str | None = None


def _resolved_examination(
    media_obj: RawPdfFile | VideoFile,
) -> Examination | None:
    sensitive_meta = media_obj.sensitive_meta
    if sensitive_meta is None:
        return None

    pseudo_examination = sensitive_meta.pseudo_examination
    if pseudo_examination is not None:
        return pseudo_examination.examination
    return None


def _link_video_primary_examination(
    *, video: VideoFile, patient_examination: PatientExamination
) -> None:
    existing_primary = None
    try:
        existing_primary = video.patient_examination
    except PatientExamination.DoesNotExist:
        existing_primary = None

    if (
        patient_examination.video_id is not None
        and patient_examination.video_id != video.pk
    ):
        raise ValueError(
            "patient_examination is already linked to a different primary video"
        )

    if existing_primary is not None and existing_primary.pk != patient_examination.pk:
        existing_primary.video = None
        existing_primary.save(update_fields=["video"])

    if patient_examination.video_id != video.pk:
        patient_examination.video = video
        patient_examination.save(update_fields=["video"])


def link_media_to_patient_examination(
    *,
    media_type: Literal["video", "pdf"],
    media_obj: RawPdfFile | VideoFile,
    patient_examination: PatientExamination,
) -> None:
    update_fields: list[str] = []

    if media_obj.examination_id != patient_examination.pk:
        media_obj.examination = patient_examination
        update_fields.append("examination")
    if media_obj.patient_id != patient_examination.patient_id:
        media_obj.patient = patient_examination.patient
        update_fields.append("patient")

    if update_fields:
        media_obj.save(update_fields=update_fields)

    if media_type == "video":
        assert isinstance(media_obj, VideoFile)
        _link_video_primary_examination(
            video=media_obj, patient_examination=patient_examination
        )


def _hydrate_inferred_patient_examination(
    *,
    patient_examination: PatientExamination,
    media_obj: RawPdfFile | VideoFile,
) -> None:
    sensitive_meta = media_obj.sensitive_meta
    if sensitive_meta is None:
        return

    update_fields: list[str] = []
    inferred_examination = _resolved_examination(media_obj)

    if patient_examination.patient_id is None and sensitive_meta.pseudo_patient_id:
        patient_examination.patient = sensitive_meta.pseudo_patient
        update_fields.append("patient")
    if patient_examination.examination_id is None and inferred_examination is not None:
        patient_examination.examination = inferred_examination
        update_fields.append("examination")
    if patient_examination.date_start is None and sensitive_meta.examination_date:
        patient_examination.date_start = sensitive_meta.examination_date
        update_fields.append("date_start")

    if update_fields:
        patient_examination.save(update_fields=update_fields)


@transaction.atomic
def auto_resolve_media_case(
    *,
    media_type: Literal["video", "pdf"],
    media_obj: RawPdfFile | VideoFile,
) -> AutoCaseResolutionResult:
    if media_obj.examination_id is not None:
        patient_examination = (
            PatientExamination.objects.select_related("patient", "examination")
            .filter(pk=media_obj.examination_id)
            .first()
        )
        case_resolution_meta = get_case_resolution_meta(media_obj)
        if (
            patient_examination is not None
            and not case_resolution_meta.get("is_explicitly_resolved")
            and not case_resolution_meta.get("deferred")
        ):
            persist_auto_case_resolution_state(
                media_obj=media_obj,
                patient_examination_id=patient_examination.pk,
                patient_id=patient_examination.patient_id,
                created=False,
            )
        return AutoCaseResolutionResult(
            status="linked",
            patient_examination=patient_examination,
            created=False,
            reason="already_linked",
        )

    sensitive_meta = media_obj.sensitive_meta
    if sensitive_meta is None:
        return AutoCaseResolutionResult(
            status="unresolved", reason="missing_sensitive_meta"
        )

    patient_hash = sensitive_meta.patient_hash
    examination_hash = sensitive_meta.examination_hash
    if not patient_hash or not examination_hash:
        return AutoCaseResolutionResult(status="unresolved", reason="missing_hashes")

    exact_matches = list(
        PatientExamination.objects.select_related("patient", "examination")
        .filter(hash=examination_hash)
        .order_by("-id")
    )
    if len(exact_matches) > 1:
        return AutoCaseResolutionResult(status="ambiguous", reason="multiple_matches")

    if exact_matches:
        patient_examination = exact_matches[0]
        _hydrate_inferred_patient_examination(
            patient_examination=patient_examination,
            media_obj=media_obj,
        )
        link_media_to_patient_examination(
            media_type=media_type,
            media_obj=media_obj,
            patient_examination=patient_examination,
        )
        persist_auto_case_resolution_state(
            media_obj=media_obj,
            patient_examination_id=patient_examination.pk,
            patient_id=patient_examination.patient_id,
            created=False,
        )
        return AutoCaseResolutionResult(
            status="linked",
            patient_examination=patient_examination,
            created=False,
            reason="matched_by_hash",
        )

    inferred_examination = _resolved_examination(media_obj)
    patient_examination, created = (
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            patient_hash=patient_hash,
            examination_hash=examination_hash,
            examination_name=(
                inferred_examination.name if inferred_examination is not None else None
            ),
        )
    )
    _hydrate_inferred_patient_examination(
        patient_examination=patient_examination,
        media_obj=media_obj,
    )
    link_media_to_patient_examination(
        media_type=media_type,
        media_obj=media_obj,
        patient_examination=patient_examination,
    )
    persist_auto_case_resolution_state(
        media_obj=media_obj,
        patient_examination_id=patient_examination.pk,
        patient_id=patient_examination.patient_id,
        created=created,
    )
    return AutoCaseResolutionResult(
        status="linked",
        patient_examination=patient_examination,
        created=created,
        reason="created_from_sensitive_meta" if created else "reused_by_hash",
    )


__all__ = [
    "AutoCaseResolutionResult",
    "auto_resolve_media_case",
    "link_media_to_patient_examination",
]
