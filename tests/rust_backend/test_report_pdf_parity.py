from __future__ import annotations

import pytest

from endoreg_db.utils.rust_backend import (
    render_single_page_pdf as rust_render_single_page_pdf,
)
from tests.helpers.pdf import render_single_page_pdf

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "text",
    [
        "",
        "line one\nline two",
        r"text with \(parens\) and \ slashes",
        "emoji \U0001f600 replacement",
        "\n".join(f"line {index}" for index in range(80)),
        "carriage\rreturn\r\nmix",
    ],
)
def test_report_pdf_rust_backend_matches_python_reference(text: str) -> None:
    rendered = rust_render_single_page_pdf(text)
    if rendered is None:
        pytest.skip("The optional native single-page PDF helper is unavailable.")
    assert rendered == render_single_page_pdf(text)


def test_native_pdf_renderer_reports_unavailable_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("endoreg_db.utils.rust_backend._render_single_page_pdf", None)
    assert rust_render_single_page_pdf("report") is None
