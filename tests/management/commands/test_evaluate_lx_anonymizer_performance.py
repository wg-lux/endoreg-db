from django.core.management.base import CommandError
import pytest

from endoreg_db.management.commands.evaluate_lx_anonymizer_performance import Command


def test_evaluator_auto_discovery_excludes_text_reports(tmp_path):
    text_report = tmp_path / "report.txt"
    text_report.write_text("report text", encoding="utf-8")

    discovered = Command()._discover_inputs(
        paths=[str(text_report)],
        forced_media_type="auto",
        recursive=False,
        limit=0,
    )

    assert discovered == []


def test_evaluator_forced_report_rejects_text_reports(tmp_path):
    text_report = tmp_path / "report.txt"
    text_report.write_text("report text", encoding="utf-8")

    with pytest.raises(CommandError, match="Text report inputs bypass lx_anonymizer"):
        Command()._discover_inputs(
            paths=[str(text_report)],
            forced_media_type="report",
            recursive=False,
            limit=0,
        )


def test_evaluator_discovers_pdf_reports(tmp_path):
    pdf_report = tmp_path / "report.pdf"
    pdf_report.write_bytes(b"%PDF-1.4\n")

    discovered = Command()._discover_inputs(
        paths=[str(pdf_report)],
        forced_media_type="auto",
        recursive=False,
        limit=0,
    )

    assert discovered == [(pdf_report.resolve(), "report")]
