from __future__ import annotations

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models.media.pdf.pdf_processing_history import PdfProcessingHistory
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile


def _pdf() -> RawPdfFile:
    return RawPdfFile.objects.create(pdf_hash=f"pdf-history-{uuid4().hex}")


@pytest.mark.django_db
def test_pdf_processing_history_canonicalizes_manifest_on_direct_save() -> None:
    history = PdfProcessingHistory.objects.create(
        pdf=_pdf(),
        source_type=PdfProcessingHistory.SOURCE_TYPE_RAW,
        redaction_manifest={
            "version": 1,
            "normalized": True,
            "pages": [
                {
                    "page": 1,
                    "boxes": [
                        {
                            "x": 0,
                            "y": 0,
                            "width": 0.25,
                            "height": 0.5,
                        }
                    ],
                }
            ],
        },
    )

    assert history.redaction_manifest == {
        "version": 1,
        "normalized": True,
        "pages": [
            {
                "page": 1,
                "boxes": [
                    {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 0.25,
                        "height": 0.5,
                    }
                ],
            }
        ],
    }

    history.refresh_from_db()
    assert history.redaction_manifest["version"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"version": 1, "normalized": True, "pages": [], "unknown": "value"},
        {
            "version": 1,
            "normalized": True,
            "pages": [
                {
                    "page": 1,
                    "boxes": [
                        {
                            "x": 0.9,
                            "y": 0.0,
                            "width": 0.2,
                            "height": 0.1,
                        }
                    ],
                }
            ],
        },
    ],
)
def test_pdf_processing_history_rejects_invalid_direct_manifest_writes(
    manifest: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        PdfProcessingHistory.objects.create(
            pdf=_pdf(),
            source_type=PdfProcessingHistory.SOURCE_TYPE_RAW,
            redaction_manifest=manifest,
        )

    assert "redaction_manifest" in exc_info.value.message_dict
