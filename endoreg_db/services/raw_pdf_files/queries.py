from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile


def _raw_pdf_model():
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

    return RawPdfFile


def get_raw_pdf_by_pk(pk: int) -> "RawPdfFile":
    model = _raw_pdf_model()
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist as exc:
        raise ValueError(f"report with ID {pk} does not exist.") from exc


def get_raw_pdf_by_content_hash(content_hash: str) -> "RawPdfFile":
    model = _raw_pdf_model()
    try:
        return model.objects.get(pdf_hash=content_hash)
    except model.DoesNotExist as exc:
        raise ValueError(f"report with ID {content_hash} does not exist.") from exc


def raw_pdf_hash_exists(
    content_hash: str,
    *,
    model_cls: type["RawPdfFile"] | None = None,
) -> bool:
    model = model_cls or _raw_pdf_model()
    return bool(content_hash) and model.objects.filter(pdf_hash=content_hash).exists()
