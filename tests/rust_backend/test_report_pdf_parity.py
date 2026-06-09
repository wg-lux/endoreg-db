from __future__ import annotations

from unittest.mock import patch

import pytest

from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.utils.system.rust_backend import (
    render_single_page_pdf as rust_render_single_page_pdf,
)


def _python_escape_pdf_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _python_render_single_page_pdf(text: str) -> bytes:
    normalized_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    max_lines = 65
    lines = normalized_lines[:max_lines] if normalized_lines else [""]
    commands = ["BT", "/F1 10 Tf", "36 806 Td"]
    for idx, raw_line in enumerate(lines):
        safe_line = raw_line.encode("latin-1", "replace").decode("latin-1")
        commands.append(f"({_python_escape_pdf_text(safe_line)}) Tj")
        if idx < len(lines) - 1:
            commands.append("0 -12 Td")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]

    payload = b"%PDF-1.4\n"
    offsets = [0]
    for obj_index, obj_payload in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload += f"{obj_index} 0 obj\n".encode("ascii")
        payload += obj_payload
        payload += b"\nendobj\n"

    startxref = len(payload)
    payload += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    payload += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        payload += f"{offset:010d} 00000 n \n".encode("ascii")
    payload += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode(
        "ascii"
    )
    return payload


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
    expected = _python_render_single_page_pdf(text)
    rendered = ReportImportService._render_single_page_pdf(text)

    assert rendered == expected

    rust_rendered = rust_render_single_page_pdf(text)
    if rust_rendered is not None:
        assert rust_rendered == expected


def test_report_pdf_service_falls_back_to_python_renderer_when_rust_path_returns_none() -> (
    None
):
    import endoreg_db.import_files.report_import_service as report_import_service_module

    text = "line one\nline two"
    expected = _python_render_single_page_pdf(text)

    with patch.object(
        report_import_service_module,
        "rust_render_pdf",
        return_value=None,
    ):
        assert ReportImportService._render_single_page_pdf(text) == expected
