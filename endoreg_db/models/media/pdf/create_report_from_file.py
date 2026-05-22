from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type, Union

from endoreg_db.services.raw_pdf_files import create_raw_pdf_file_from_path

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf import RawPdfFile


def _create_from_file(
    cls_model: Type["RawPdfFile"],
    file_path: Union[str, Path],
    center_name: Optional[str] = None,
    save: bool = True,
    **kwargs,
) -> "RawPdfFile":
    """
    Compatibility wrapper for legacy imports.

    New code should use endoreg_db.services.raw_pdf_files.create_raw_pdf_file_from_path.
    """
    return create_raw_pdf_file_from_path(
        file_path=file_path,
        center_name=center_name,
        model_cls=cls_model,
        save=save,
        **kwargs,
    )
