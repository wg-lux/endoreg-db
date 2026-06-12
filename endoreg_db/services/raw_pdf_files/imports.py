from __future__ import annotations


import logging
import os
from pathlib import Path
from typing import Protocol, cast
from django.core.files.storage import Storage
from typing import TYPE_CHECKING, Protocol, TypedDict, Unpack, cast

from endoreg_db.utils.file_operations import get_content_hash_filename
from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    path_reference,
)

from .state import get_or_create_raw_pdf_state
from .types import ReportPdfArtifactKind

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

logger = logging.getLogger(__name__)

class _StoredFileLike(Protocol):
    name: str
    storage: Storage

class _RawPdfFileCreateKwargs(TypedDict, total=False):
    pass


class _RawPdfFilePersistence(Protocol):
    pk: int
    pdf_hash: str

    def save(self, *args: object, **kwargs: object) -> None: ...

    def delete(self, *args: object, **kwargs: object) -> tuple[int, dict[str, int]]: ...


def _raw_pdf_model():
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

    return RawPdfFile


def create_raw_pdf_file_from_path(
    file_path: str | Path,
    center_name: str | None = None,
    *,
    model_cls: type["RawPdfFile"] | None = None,
    save: bool = True,
    **kwargs: Unpack[_RawPdfFileCreateKwargs],
) -> "RawPdfFile":
    from endoreg_db.models.administration.center.center import Center

    model = model_cls or _raw_pdf_model()
    if isinstance(file_path, str):
        file_path = Path(file_path)

    if not file_path.exists():
        logger.error("Source file does not exist: %s", file_path)
        raise FileNotFoundError(f"Source file not found: {file_path}")

    if not center_name:
        try:
            center_name = os.environ["CENTER_NAME"]
        except KeyError:
            logger.error("Center name must be provided or set in CENTER_NAME env var.")
            raise ValueError("Center name must be provided.")

    try:
        center = Center.objects.get(name=center_name)
    except Center.DoesNotExist as exc:
        logger.error("Center '%s' not found.", center_name)
        raise ValueError(f"Center '{center_name}' not found.") from exc

    try:
        pdf_hash = get_pdf_hash(file_path)
        logger.debug("Calculated PDF hash: %s", pdf_hash)
    except Exception as exc:
        logger.error("Could not calculate hash for %s: %s", file_path, exc)
        raise ValueError(f"Could not calculate hash for {file_path}") from exc

    existing_pdf_file = model.objects.filter(pdf_hash=pdf_hash).first()
    if existing_pdf_file:
        logger.warning(
            "RawPdfFile with hash %s already exists (ID: %s)",
            pdf_hash,
            existing_pdf_file.pk,
        )

        field_file = existing_pdf_file.file
        file_name = field_file.name if field_file else None
        if file_name and cast(_StoredFileLike, field_file).storage.exists(file_name):
            logger.warning("File is present. Returning existing instance.")
            return existing_pdf_file

        logger.warning(
            "RawPdfFile exists but file is missing. Deleting orphaned record."
        )
        existing_pdf = cast(_RawPdfFilePersistence, existing_pdf_file)
        existing_pdf.delete()

    new_file_name, _uuid = get_content_hash_filename(file_path)
    try:
        raw_pdf = model(
            pdf_hash=pdf_hash,
            center=center,
            **kwargs,
        )
        saved_name = save_local_file(
            raw_pdf.file,
            file_path,
            name=new_file_name,
            save=False,
        )

        if save:
            raw_pdf_persistence = cast(_RawPdfFilePersistence, raw_pdf)
            raw_pdf_persistence.save()
            logger.info("Successfully created RawPdfFile PK %s", raw_pdf.pk)

        raw_pdf_persistence = cast(_RawPdfFilePersistence, raw_pdf)
        emit_structured_event(
            logger,
            "raw_pdf.file_saved",
            status="ok",
            report_id=raw_pdf.pk,
            pdf_hash=raw_pdf_persistence.pdf_hash,
            artifact_kind=ReportPdfArtifactKind.RAW.value,
            source_path=path_reference(file_path),
            storage_name=saved_name,
        )
        return raw_pdf

    except Exception as exc:
        logger.error("Error processing or saving file %s: %s", file_path, exc)
        raise RuntimeError(f"PDF processing failed: {exc}") from exc


def create_initialized_raw_pdf_file_from_path(
    file_path: str | Path,
    center_name: str | None = None,
    *,
    model_cls: type["RawPdfFile"] | None = None,
    **kwargs: Unpack[_RawPdfFileCreateKwargs],
) -> "RawPdfFile":
    raw_pdf = create_raw_pdf_file_from_path(
        file_path=file_path,
        center_name=center_name,
        model_cls=model_cls,
        **kwargs,
    )
    return initialize_raw_pdf_file(raw_pdf)


def initialize_raw_pdf_file(report: "RawPdfFile") -> "RawPdfFile":
    report.state = get_or_create_raw_pdf_state(report)
    report_persistence = cast(_RawPdfFilePersistence, report)
    report_persistence.save(update_fields=["state"])
    return report
