# endoreg_db/import_files/storage/sensitive_meta_storage.py
from typing import Union, Dict, Any

from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.metadata import SensitiveMeta
from endoreg_db.import_files.processing.sensitive_meta_adapter import (
    normalize_lx_sensitive_meta,
)
from logging import getLogger

logger = getLogger(__name__)

def sensitive_meta_storage(
    sensitive_meta: Dict[str, Any],
    instance: Union[RawPdfFile, VideoFile],
) -> bool:
    """
    Merge lx_anonymizer.SensitiveMeta into instance.sensitive_meta in the DB.

    - Normalizes the dataclass into the dict format expected by the model logic
    - Delegates to SensitiveMeta.update_from_dict() (which already calls logic.update_*)
    """
    local_meta = instance.sensitive_meta  # Django SensitiveMeta model instance
    assert isinstance(local_meta, SensitiveMeta)

    try:
        payload = normalize_lx_sensitive_meta(sensitive_meta)
        local_meta.update_from_dict(payload)  # this calls your big logic.update_*
    except Exception as e:
        logger.error(f"{e}")
        return False

    return True
