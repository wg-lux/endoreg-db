from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile


class _ReportRecord:
    pdf_hash = "a" * 64
    text = ""
    anonymized_text = ""

    def save(self, *args: object, **kwargs: object) -> None:
        return None


class _V2Reader:
    request: object | None = None

    def process_report_v2(self, request: object) -> object:
        self.request = request
        output_directory = getattr(request, "output_directory")
        attempt_id = getattr(request, "attempt_id")
        assert isinstance(output_directory, Path)
        artifact_path = output_directory / f"{attempt_id}.pdf"
        artifact_path.write_bytes(b"%PDF-1.4\nanonymized\n%%EOF\n")
        return SimpleNamespace(
            original_text="original",
            anonymized_text="anonymized",
            extracted_metadata={},
            artifact_path=artifact_path,
        )


class _LegacyReader:
    output_path: Path | None = None

    def process_report(
        self,
        *,
        pdf_path: Path,
        create_anonymized_pdf: bool,
        anonymized_pdf_output_path: str,
    ) -> tuple[str, str, dict[str, object], Path]:
        assert pdf_path.is_file()
        assert create_anonymized_pdf
        self.output_path = Path(anonymized_pdf_output_path)
        self.output_path.write_bytes(b"%PDF-1.4\nlegacy-anonymized\n%%EOF\n")
        return "legacy-original", "legacy-anonymized", {}, self.output_path


@pytest.mark.unit
def test_report_anonymizer_prefers_v2_attempt_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    source = tmp_path / "sensitive.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    reader = _V2Reader()
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_processed_report_dir", lambda: tmp_path / "processed")

    def fake_import_module(name: str) -> SimpleNamespace:
        return SimpleNamespace(
            ReportAnonymizationRequestV2=SimpleNamespace,
        )

    monkeypatch.setattr(
        module.importlib,
        "import_module",
        fake_import_module,
    )

    def fake_reader(self: ReportAnonymizer) -> _V2Reader:
        return reader

    monkeypatch.setattr(
        ReportAnonymizer,
        "_instantiate_report_reader",
        fake_reader,
    )

    def persist_metadata(metadata: object, report: object) -> bool:
        return True

    monkeypatch.setattr(
        module,
        "sensitive_meta_storage",
        persist_metadata,
    )
    ctx = ImportContext(
        file_path=source,
        original_path=source,
        center_name="dummy-center",
        file_type="report",
        file_hash="b" * 64,
        current_report=cast(RawPdfFile, _ReportRecord()),
    )

    result = anonymizer.anonymize_report(ctx)

    assert reader.request is not None
    assert getattr(reader.request, "source_path") == source
    assert getattr(reader.request, "source_sha256") == "b" * 64
    output_directory = getattr(reader.request, "output_directory")
    assert isinstance(output_directory, Path)
    assert output_directory.parent == tmp_path / "processed"
    assert result.anonymized_path is not None
    assert result.anonymized_path.parent == output_directory


@pytest.mark.unit
def test_report_anonymizer_supports_legacy_tuple_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    source = tmp_path / "sensitive.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    reader = _LegacyReader()
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_processed_report_dir", lambda: tmp_path / "processed")

    def fake_reader(self: ReportAnonymizer) -> _LegacyReader:
        return reader

    def persist_metadata(metadata: object, report: object) -> bool:
        return True

    monkeypatch.setattr(ReportAnonymizer, "_instantiate_report_reader", fake_reader)
    monkeypatch.setattr(
        module,
        "sensitive_meta_storage",
        persist_metadata,
    )
    ctx = ImportContext(
        file_path=source,
        original_path=source,
        center_name="dummy-center",
        file_type="report",
        file_hash="c" * 64,
        current_report=cast(RawPdfFile, _ReportRecord()),
    )

    with caplog.at_level("WARNING"):
        result = anonymizer.anonymize_report(ctx)

    assert reader.output_path == tmp_path / "processed" / f"{'a' * 64}.pdf"
    assert result.anonymized_path == reader.output_path
    assert "legacy report tuple contract" in caplog.text
