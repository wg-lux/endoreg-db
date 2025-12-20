# endoreg_db/import_files/processing/create_sensitive_meta.py

import os
import logging
from datetime import date
from typing import Union

from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta  # adjust path

logger = logging.getLogger(__name__)

DEFAULT_PATIENT_FIRST_NAME = "unknown"
DEFAULT_PATIENT_LAST_NAME = "unknown"
DEFAULT_CENTER_NAME = "endoreg_db_demo"
# DEFAULT_PATIENT_DOB can be a fixed date or None to let logic.generate_random_dob handle it
DEFAULT_PATIENT_DOB = date(1970, 1, 1)


def default_sensitive_meta(
    instance: Union[RawPdfFile, VideoFile],
) -> SensitiveMeta | None:
    """
    Ensure the given instance has a minimal SensitiveMeta attached.

    Called after text extraction + merging; only creates meta if none exists.
    """
    if instance is None:
        logger.warning("No instance available for ensuring default patient data")
        return

    if instance.sensitive_meta:
        # Already has meta; nothing to do
        return

    logger.info(
        "No SensitiveMeta found for report %s, creating default",
        getattr(instance, "pdf_hash", instance.pk),
    )
    if not isinstance(instance.center.name, str):
        try:
            center_name = os.environ.get("DEFAULT_CENTER_NAME")
            assert center_name is not None
            instance.center.name = center_name
        except AssertionError as e:
            logger.debug(
                f"{e}Center name is not set! You can set it in .env under DEFAULT_CENTER_NAME using default from default_sensitive_meta"
            )
            instance.center.name = DEFAULT_CENTER_NAME
            instance.center.get_by_name(DEFAULT_CENTER_NAME)

    default_data = {
        "patient_first_name": DEFAULT_PATIENT_FIRST_NAME,
        "patient_last_name": DEFAULT_PATIENT_LAST_NAME,
        "patient_dob": DEFAULT_PATIENT_DOB,
        "examination_date": date.today(),
        "center_name": (
            instance.center.name
            if getattr(instance, "center", None) is not None
            else DEFAULT_CENTER_NAME
        ),
        # optional: link file_path for debugging/tracing
        "file_path": str(instance.file_path)
        if getattr(instance, "file_path", None)
        else None,
    }

    try:
        meta = SensitiveMeta.create_from_dict(default_data)
        instance.sensitive_meta = meta
        instance.save(update_fields=["sensitive_meta"])
        logger.info(
            "Created default SensitiveMeta for report %s",
            getattr(instance, "pdf_hash", instance.pk),
        )
        return meta
    except Exception as e:
        logger.error(
            "Failed to create default SensitiveMeta for report %s: %s",
            getattr(instance, "pdf_hash", instance.pk),
            e,
        )
        return None
