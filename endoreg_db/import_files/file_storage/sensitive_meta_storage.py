# endoreg_db/import_files/storage/sensitive_meta_storage.py
from typing import Union

from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.metadata import SensitiveMeta
from endoreg_db.import_files.processing.sensitive_meta_adapter import (
    normalize_lx_sensitive_meta,
)
from endoreg_db.import_files.context.default_sensitive_meta import (
    default_sensitive_meta,
)
from logging import getLogger
from lx_anonymizer.sensitive_meta_interface import SensitiveMeta as LxSM
#

logger = getLogger(__name__)


def sensitive_meta_storage(
    sensitive_meta: LxSM,
    instance: Union[RawPdfFile, VideoFile],
) -> bool:
    """
    Merge lx_anonymizer.SensitiveMeta into instance.sensitive_meta in the DB.

    - Normalizes the dataclass into the dict format expected by the model logic
    - Delegates to SensitiveMeta.update_from_dict() (which already calls logic.update_*)
    """
    local_meta = instance.sensitive_meta  # Django SensitiveMeta model instance
    if not isinstance(local_meta, SensitiveMeta):
        # If sensitice meta doesnt exist yet, ensure it
        local_meta = default_sensitive_meta(instance)
    assert isinstance(local_meta, SensitiveMeta)

    try:
        payload = normalize_lx_sensitive_meta(sensitive_meta)
        local_meta.update_from_dict(payload)  # this calls your big logic.update_*
    except Exception as e:
        logger.error(f"{e}")
        return False

    return True
