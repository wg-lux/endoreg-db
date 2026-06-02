# endoreg_db/import_files/storage/sensitive_meta_storage.py
from logging import getLogger
from typing import Union

from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.context.default_sensitive_meta import (
    default_sensitive_meta,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

#

logger = getLogger(__name__)


def sensitive_meta_storage(
    sensitive_meta: LxSensitiveMeta,
    instance: Union[RawPdfFile, VideoFile],
) -> bool:
    """
    Merge lx_anonymizer.SensitiveMeta into instance.sensitive_meta in the DB.

    - Delegates normalization and persistence to SensitiveMeta.update_from_dict()
    """
    local_meta = instance.sensitive_meta  # Django SensitiveMeta model instance
    if not isinstance(local_meta, SensitiveMeta):
        # If sensitive meta does not exist yet, ensure it.
        local_meta = default_sensitive_meta(instance)

    if not isinstance(local_meta, SensitiveMeta):
        logger.error(
            "Could not create SensitiveMeta for %s(pk=%s)",
            instance.__class__.__name__,
            instance.pk,
        )
        return False

    try:
        local_meta.update_from_lx_sensitive_meta(sensitive_meta)
    except Exception as e:
        logger.exception(
            "Failed to update SensitiveMeta(pk=%s) from lx sensitive meta: %s",
            local_meta.pk,
            e,
        )
        return False

    return True
