from __future__ import annotations

from typing import cast

from django.utils import timezone
from lx_dtypes.models.contracts import CaseResolutionRequest

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile

CASE_RESOLUTION_META_KEY = "case_resolution"


def _meta_field_name(media_obj: RawPdfFile | VideoFile) -> str:
    if isinstance(media_obj, RawPdfFile):
        return "raw_meta"
    return "meta"


def get_media_meta(media_obj: RawPdfFile | VideoFile) -> dict[str, object]:
    meta = getattr(media_obj, _meta_field_name(media_obj), None)
    if isinstance(meta, dict):
        return cast(dict[str, object], meta).copy()
    return {}


def get_case_resolution_meta(media_obj: RawPdfFile | VideoFile) -> dict[str, object]:
    media_meta = get_media_meta(media_obj)
    case_resolution_meta = media_meta.get(CASE_RESOLUTION_META_KEY)
    if isinstance(case_resolution_meta, dict):
        return cast(dict[str, object], case_resolution_meta).copy()
    return {}


def persist_case_resolution_state(
    *,
    media_obj: RawPdfFile | VideoFile,
    payload: CaseResolutionRequest,
    patient_examination_id: int | None,
    patient_id: int | None,
) -> None:
    media_meta = get_media_meta(media_obj)
    case_resolution_meta = get_case_resolution_meta(media_obj)
    action = payload.action
    case_resolution_meta.update(
        {
            "last_action": action,
            "updated_at": timezone.now().isoformat(),
            "is_explicitly_resolved": action in {"attach", "create"},
            "linked_patient_examination_id": patient_examination_id,
            "linked_patient_id": patient_id,
            "deferred": action == "defer",
        }
    )
    media_meta[CASE_RESOLUTION_META_KEY] = case_resolution_meta

    field_name = _meta_field_name(media_obj)
    setattr(media_obj, field_name, media_meta)
    media_obj.save(update_fields=[field_name])


def persist_auto_case_resolution_state(
    *,
    media_obj: RawPdfFile | VideoFile,
    patient_examination_id: int,
    patient_id: int,
    created: bool,
) -> None:
    media_meta = get_media_meta(media_obj)
    case_resolution_meta = get_case_resolution_meta(media_obj)
    case_resolution_meta.update(
        {
            "last_action": "auto_create" if created else "auto_attach",
            "updated_at": timezone.now().isoformat(),
            "is_explicitly_resolved": False,
            "is_auto_resolved": True,
            "linked_patient_examination_id": patient_examination_id,
            "linked_patient_id": patient_id,
            "deferred": False,
        }
    )
    media_meta[CASE_RESOLUTION_META_KEY] = case_resolution_meta

    field_name = _meta_field_name(media_obj)
    setattr(media_obj, field_name, media_meta)
    media_obj.save(update_fields=[field_name])


__all__ = [
    "CASE_RESOLUTION_META_KEY",
    "get_case_resolution_meta",
    "get_media_meta",
    "persist_auto_case_resolution_state",
    "persist_case_resolution_state",
]
