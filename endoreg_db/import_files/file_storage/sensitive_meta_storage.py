# endoreg_db/import_files/storage/sensitive_meta_storage.py
from typing import Protocol, cast

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


def persist_sensitive_meta_candidate(
    *,
    instance: RawPdfFile | VideoFile,
    candidate: LxSensitiveMeta,
) -> SensitiveMeta:
    """
    Persist the extracted SensitiveMeta into ``instance.sensitive_meta``.

    - Delegates normalization and persistence to SensitiveMeta.update_from_dict()
    """
    typed_instance = cast(_SensitiveMetaCarrier, instance)
    local_meta = typed_instance.sensitive_meta
    if not isinstance(local_meta, SensitiveMeta):
        # If sensitive meta does not exist yet, ensure it.
        local_meta = default_sensitive_meta(instance)

    if not isinstance(local_meta, SensitiveMeta):
        raise RuntimeError(
            f"Could not create SensitiveMeta for {instance.__class__.__name__}(pk={instance.pk})."
        )

    return local_meta.update_from_lx_sensitive_meta(candidate)
