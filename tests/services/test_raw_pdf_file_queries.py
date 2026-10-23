from __future__ import annotations

import pytest

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.services.raw_pdf_files import (
    get_raw_pdf_by_content_hash,
    get_raw_pdf_by_pk,
    raw_pdf_hash_exists,
)


@pytest.fixture
def report() -> RawPdfFile:
    return RawPdfFile.objects.create(pdf_hash="raw-pdf-query-hash")


@pytest.mark.django_db
def test_raw_pdf_query_services_return_matching_report(report: RawPdfFile) -> None:
    assert raw_pdf_hash_exists(report.pdf_hash) is True
    assert raw_pdf_hash_exists("") is False
    assert get_raw_pdf_by_pk(report.pk) == report
    assert get_raw_pdf_by_content_hash(report.pdf_hash) == report


@pytest.mark.django_db
def test_raw_pdf_primary_key_query_preserves_missing_report_contract() -> None:
    with pytest.raises(
        ValueError,
        match=r"^report with ID 999999 does not exist\.$",
    ) as exc_info:
        get_raw_pdf_by_pk(999_999)

    assert isinstance(exc_info.value.__cause__, RawPdfFile.DoesNotExist)


@pytest.mark.django_db
def test_raw_pdf_hash_query_preserves_missing_report_contract() -> None:
    with pytest.raises(
        ValueError,
        match=r"^report with ID missing-report-hash does not exist\.$",
    ) as exc_info:
        get_raw_pdf_by_content_hash("missing-report-hash")

    assert isinstance(exc_info.value.__cause__, RawPdfFile.DoesNotExist)


def test_raw_pdf_query_facades_are_retired() -> None:
    retired_names = ("get_report_by_pk", "get_report_by_hash")

    assert [name for name in retired_names if hasattr(RawPdfFile, name)] == []
