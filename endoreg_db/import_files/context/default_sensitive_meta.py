# endoreg_db/import_files/processing/create_sensitive_meta.py

import logging
import os
from datetime import date
from types import NoneType
from typing import Protocol, cast

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta  # adjust path

logger = logging.getLogger(__name__)

DEFAULT_PATIENT_FIRST_NAME = "unknown"
DEFAULT_PATIENT_LAST_NAME = "unknown"
DEFAULT_CENTER_NAME = "endoreg_db_demo"
DEFAULT_PATIENT_DOB = date(1970, 1, 1)

type DefaultSensitiveMetaValue = str | date
type DefaultSensitiveMetaData = dict[str, DefaultSensitiveMetaValue]


class _NamedCenter(Protocol):
    name: str


class _SensitiveMetaCarrier(Protocol):
    pk: int
    center: Center | NoneType
    sensitive_meta: SensitiveMeta | NoneType

    def save(self, *, update_fields: list[str]) -> None: ...


class _RawPdfIdentifier(Protocol):
    pdf_hash: str


def _center_name(center: Center | NoneType) -> str:
    if center is None:
        return DEFAULT_CENTER_NAME
    named_center = cast(_NamedCenter, center)
    if named_center.name:
        return named_center.name
    return os.environ.get("CENTER_NAME", DEFAULT_CENTER_NAME)


def _instance_log_identifier(instance: _SensitiveMetaCarrier) -> str:
    if isinstance(instance, RawPdfFile):
        raw_pdf = cast(_RawPdfIdentifier, instance)
        return raw_pdf.pdf_hash
    return str(instance.pk)


def default_sensitive_meta(
    instance: RawPdfFile | VideoFile | NoneType,
) -> SensitiveMeta | NoneType:
    """
    Ensure the given instance has a minimal SensitiveMeta attached.

    Called after text extraction + merging; only creates meta if none exists.
    """
    if instance is None:
        logger.warning("No instance available for ensuring default patient data")
        return None

    typed_instance = cast(_SensitiveMetaCarrier, instance)
    if typed_instance.sensitive_meta is not None:
        # Already has meta; nothing to do
        return None

    center_name = _center_name(typed_instance.center)
    logger.info(
        "No SensitiveMeta found for report %s, creating default",
        _instance_log_identifier(typed_instance),
    )

    default_data: DefaultSensitiveMetaData = {
        "patient_first_name": DEFAULT_PATIENT_FIRST_NAME,
        "patient_last_name": DEFAULT_PATIENT_LAST_NAME,
        "patient_dob": DEFAULT_PATIENT_DOB,
        "examination_date": date.today(),
        "center_name": center_name,
    }

    try:
        meta = SensitiveMeta.create_from_dict(default_data)
        typed_instance.sensitive_meta = meta
        typed_instance.save(update_fields=["sensitive_meta"])
        logger.info(
            "Created default SensitiveMeta for report %s",
            _instance_log_identifier(typed_instance),
        )
        return meta
    except Exception as e:
        logger.error(
            "Failed to create default SensitiveMeta for report %s: %s",
            _instance_log_identifier(typed_instance),
            e,
        )
        return None
