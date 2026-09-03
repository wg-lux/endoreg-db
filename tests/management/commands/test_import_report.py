from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command

from endoreg_db.management.commands import import_report as command_module
from endoreg_db.utils.file_operations import atomic_write_file


def test_import_report_missing_file_reports_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(command_module, "load_all_reference_data", lambda: None)
    output = StringIO()
    missing_path = tmp_path / "missing.pdf"

    call_command("import_report", str(missing_path), stdout=output)

    assert f"Report file not found: {missing_path}" in output.getvalue()


def test_import_report_delegates_and_writes_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "report.pdf"
    source_content = b"%PDF-1.4\n%%EOF\n"
    atomic_write_file(
        destination=source_path,
        content=(source_content,),
        required_bytes=len(source_content),
    )
    report = SimpleNamespace(
        pk=17,
        pdf_hash="report-hash",
        text="source text",
        anonymized_text="safe text",
        sensitive_meta=SimpleNamespace(pk=23),
        file=SimpleNamespace(name="report.pdf"),
        processed_file=SimpleNamespace(name="report.processed.pdf"),
        refresh_from_db=lambda: None,
    )
    import_calls: list[tuple[Path, str, bool]] = []

    class FakeReportImportService:
        def import_and_anonymize(
            self,
            *,
            file_path: Path,
            center_name: str,
            retry: bool,
        ) -> object:
            import_calls.append((file_path, center_name, retry))
            return report

    monkeypatch.setattr(command_module, "load_all_reference_data", lambda: None)
    monkeypatch.setattr(
        command_module,
        "ReportImportService",
        FakeReportImportService,
    )
    output = StringIO()

    call_command(
        "import_report",
        str(source_path),
        "--center_name",
        "test-center",
        "--report_dir_root",
        str(tmp_path / "reports"),
        "--verbose",
        stdout=output,
    )

    assert import_calls == [(source_path, "test-center", False)]
    command_output = output.getvalue()
    assert "Imported report id=17 hash=report-hash" in command_output
    assert "text_len=11, anonymized_text_len=9, sensitive_meta_id=23" in command_output
    assert (
        "Stored file=report.pdf processed_file=report.processed.pdf" in command_output
    )
