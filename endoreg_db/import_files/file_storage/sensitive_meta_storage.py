# endoreg_db/import_files/storage/sensitive_meta_storage.py
from collections.abc import Mapping, Sized
from typing import Protocol, cast

from django.db import transaction
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.context.default_sensitive_meta import (
    default_sensitive_meta,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

#


class _SensitiveMetaCarrier(Protocol):
    pk: int
    sensitive_meta: SensitiveMeta | None


_LX_TO_LOCAL_FIELDS: Mapping[str, str] = {
    "examination_date": "examination_date",
    "examination_time": "examination_time",
    "casenumber": "casenumber",
    "gender": "patient_gender",
    "first_name": "patient_first_name",
    "last_name": "patient_last_name",
    "dob": "patient_dob",
    "endoscope_type": "endoscope_type",
    "endoscope_sn": "endoscope_sn",
    "examiner_first_name": "examiner_first_name",
    "examiner_last_name": "examiner_last_name",
    "text": "text",
    "anonymized_text": "anonymized_text",
}
_PLACEHOLDER_VALUES = frozenset(
    {"", "-", "n/a", "na", "none", "null", "undefined", "unknown"}
)


def _is_meaningful_extracted_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().casefold() not in _PLACEHOLDER_VALUES
    if isinstance(value, (list, dict, set, tuple)):
        return len(cast(Sized, value)) > 0
    return True


def _candidate_updates(candidate: LxSensitiveMeta) -> dict[str, object]:
    """Return only explicitly extracted, meaningful, locally writable fields."""
    updates: dict[str, object] = {}
    for lx_field in candidate.model_fields_set:
        local_field = _LX_TO_LOCAL_FIELDS.get(lx_field)
        if local_field is None:
            # Ownership and storage fields such as center and file_path are
            # intentionally controlled by the trusted import context.
            continue
        value = getattr(candidate, lx_field)
        if _is_meaningful_extracted_value(value):
            updates[local_field] = value
    return updates


def _locked_carrier(instance: RawPdfFile | VideoFile) -> RawPdfFile | VideoFile:
    if isinstance(instance, RawPdfFile):
        return (
            RawPdfFile.objects.select_for_update(of=("self",))
            .select_related("center", "sensitive_meta")
            .get(pk=instance.pk)
        )
    return (
        VideoFile.objects.select_for_update(of=("self",))
        .select_related("center", "sensitive_meta")
        .get(pk=instance.pk)
    )


@transaction.atomic
def persist_sensitive_meta_candidate(
    *,
    instance: RawPdfFile | VideoFile,
    candidate: LxSensitiveMeta,
) -> SensitiveMeta:
    """
    Persist the extracted SensitiveMeta into ``instance.sensitive_meta``.

    Only explicitly extracted values cross this boundary. Media ownership and
    storage-path fields remain controlled by the trusted import context.
    """
    locked_instance = _locked_carrier(instance)
    typed_instance = cast(_SensitiveMetaCarrier, locked_instance)
    local_meta = typed_instance.sensitive_meta
    if not isinstance(local_meta, SensitiveMeta):
        # If sensitive meta does not exist yet, ensure it.
        local_meta = default_sensitive_meta(locked_instance)

    if not isinstance(local_meta, SensitiveMeta):
        raise RuntimeError(
            f"Could not create SensitiveMeta for {instance.__class__.__name__}(pk={instance.pk})."
        )

    locked_center_id = getattr(locked_instance, "center_id", None)
    if local_meta.center_id != locked_center_id:
        raise RuntimeError(
            "SensitiveMeta center does not match its media owner; refusing import update."
        )

    updates = _candidate_updates(candidate)
    if updates:
        local_meta = local_meta.update_from_dict(updates)

    # Keep the caller's in-memory object coherent with the row updated under
    # lock. No extra database write is needed because default_sensitive_meta()
    # attached a newly created relation to the locked row.
    instance.sensitive_meta = local_meta
    return local_meta
