from __future__ import annotations

from typing import Any

from django.utils import timezone

from endoreg_db.models import RawPdfFile, VideoFile

CASE_RESOLUTION_META_KEY = "case_resolution"


def _meta_field_name(media_obj: RawPdfFile | VideoFile) -> str:
    if isinstance(media_obj, RawPdfFile):
        return "raw_meta"
    return "meta"


def get_media_meta(media_obj: RawPdfFile | VideoFile) -> dict[str, Any]:
    meta = getattr(media_obj, _meta_field_name(media_obj), None)
    if isinstance(meta, dict):
        return dict(meta)
    return {}


def get_case_resolution_meta(media_obj: RawPdfFile | VideoFile) -> dict[str, Any]:
    media_meta = get_media_meta(media_obj)
    case_resolution_meta = media_meta.get(CASE_RESOLUTION_META_KEY)
    if isinstance(case_resolution_meta, dict):
        return dict(case_resolution_meta)
    return {}


def persist_case_resolution_state(
    *,
    media_obj: RawPdfFile | VideoFile,
    action: str,
    patient_examination_id: int | None,
    patient_id: int | None,
) -> None:
    media_meta = get_media_meta(media_obj)
    case_resolution_meta = get_case_resolution_meta(media_obj)
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


__all__ = [
    "CASE_RESOLUTION_META_KEY",
    "get_case_resolution_meta",
    "get_media_meta",
    "persist_case_resolution_state",
]
