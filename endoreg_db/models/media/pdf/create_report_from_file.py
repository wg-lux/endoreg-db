# models/data_file/import_classes/create_pdf_from_file.py
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type, Union

from django.core.files import File

from endoreg_db.utils.file_operations import get_content_hash_filename
from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.utils.paths import (
    IMPORT_REPORT_DIR,
    SENSITIVE_REPORT_DIR,
    to_storage_relative,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf import RawPdfFile

logger = logging.getLogger("raw_pdf")


def _canonical_managed_report_relative_path(file_path: Path) -> str | None:
    resolved = file_path.resolve()
    for managed_root in (SENSITIVE_REPORT_DIR, IMPORT_REPORT_DIR):
        try:
            resolved.relative_to(managed_root.resolve())
        except ValueError:
            continue
        return to_storage_relative(resolved)
    return None


def _create_from_file(
    cls_model: Type["RawPdfFile"],
    file_path: Union[str, Path],
    center_name: Optional[str] = None,
    save: bool = True,
    **kwargs,
) -> "RawPdfFile":
    """
    Creates a RawPdfFile instance from a given file path, mirroring the video pipeline.
    Handles hashing, orphaned record cleanup, and atomic DB assignment.
    """
    from endoreg_db.models.administration import Center

    # 1. Standardize Path
    if isinstance(file_path, str):
        file_path = Path(file_path)

    if not file_path.exists():
        logger.error(f"Source file does not exist: {file_path}")
        raise FileNotFoundError(f"Source file not found: {file_path}")

    # 2. Pull Center Name (with Environment Fallback)
    if not center_name:
        try:
            center_name = os.environ["CENTER_NAME"]
        except KeyError:
            logger.error("Center name must be provided or set in CENTER_NAME env var.")
            raise ValueError("Center name must be provided.")

    try:
        center = Center.objects.get(name=center_name)
    except Center.DoesNotExist as e:
        logger.error(f"Center '{center_name}' not found.")
        raise ValueError(f"Center '{center_name}' not found.") from e

    # 3. Hash Calculation
    try:
        pdf_hash = get_pdf_hash(file_path)
        logger.debug(f"Calculated PDF hash: {pdf_hash}")
    except Exception as e:
        logger.error(f"Could not calculate hash for {file_path}: {e}")
        raise ValueError(f"Could not calculate hash for {file_path}") from e

    # 4. Handle Existing Records & TOCTOU (Aligned with VideoFile)
    existing_pdf_file = cls_model.objects.filter(pdf_hash=pdf_hash).first()
    if existing_pdf_file:
        logger.warning(
            "RawPdfFile with hash %s already exists (ID: %s)",
            pdf_hash,
            existing_pdf_file.pk,
        )

        # Check if the physical file is still present
        _file = existing_pdf_file.file
        if _file and _file.storage.exists(_file.name):
            logger.warning("File is present. Returning existing instance.")
            return existing_pdf_file

        # Burn orphaned record to stay consistent with VideoFile philosophy
        logger.warning(
            "RawPdfFile exists but file is missing. Deleting orphaned record."
        )
        existing_pdf_file.delete()

    # 5. Create New Record
    managed_relative_path = _canonical_managed_report_relative_path(file_path)
    new_file_name, _uuid = get_content_hash_filename(file_path)

    try:
        if managed_relative_path is not None:
            raw_pdf = cls_model(
                pdf_hash=pdf_hash,
                center=center,
                **kwargs,
            )
            raw_pdf.file.name = managed_relative_path
            if save:
                raw_pdf.save()
                logger.info(
                    "Successfully attached managed RawPdfFile PK %s to %s",
                    raw_pdf.pk,
                    managed_relative_path,
                )
        else:
            with file_path.open("rb") as f:
                django_file = File(f, name=new_file_name)
                raw_pdf = cls_model(
                    pdf_hash=pdf_hash, center=center, file=django_file, **kwargs
                )

                if save:
                    raw_pdf.save()
                    logger.info(f"Successfully created RawPdfFile PK {raw_pdf.pk}")

        return raw_pdf

    except Exception as e:
        logger.error(f"Error processing or saving file {file_path}: {e}")
        raise RuntimeError(f"PDF processing failed: {e}") from e
