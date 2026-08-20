from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    persist_report_anonymization_result,
    persist_sensitive_meta_candidate,
    ReportAnonymizer,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from lx_dtypes.models.contracts.report_anonymization import ReportAnonymizationResult


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


def _create_report_for_tests(**kwargs: Any) -> RawPdfFile:
    center, _ = Center.objects.get_or_create(name="report-anonymization-test-center")
    data: dict[str, Any] = {
        "pdf_hash": "report-" + uuid.uuid4().hex,
        "file": "report-test.pdf",
        "center": center,
        "text": "",
        "anonymized_text": "",
    }
    data.update(kwargs)
    return RawPdfFile.objects.create(**data)


@pytest.mark.django_db
def test_report_anonymizer_prefers_v2_attempt_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base_db_data: bool,
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

    def persist_metadata(
        *, instance: RawPdfFile, candidate: LxSensitiveMeta
    ) -> SensitiveMeta:
        return SensitiveMeta.objects.create(
            center=instance.center,
            patient_first_name="unit",
        )

    monkeypatch.setattr(
        module,
        "persist_sensitive_meta_candidate",
        persist_metadata,
    )
    ctx = ImportContext(
        file_path=source,
        original_path=source,
        center_name="dummy-center",
        file_type="report",
        file_hash="b" * 64,
        current_report=_create_report_for_tests(text="", anonymized_text=""),
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
@pytest.mark.django_db
def test_report_anonymizer_supports_legacy_tuple_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    base_db_data: bool,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    source = tmp_path / "sensitive.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    reader = _LegacyReader()
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_processed_report_dir", lambda: tmp_path / "processed")

    def fake_reader(self: ReportAnonymizer) -> _LegacyReader:
        return reader

    def persist_metadata(
        *, instance: RawPdfFile, candidate: LxSensitiveMeta
    ) -> SensitiveMeta:
        return SensitiveMeta.objects.create(
            center=instance.center,
            patient_first_name="unit",
        )

    monkeypatch.setattr(ReportAnonymizer, "_instantiate_report_reader", fake_reader)
    monkeypatch.setattr(
        module,
        "persist_sensitive_meta_candidate",
        persist_metadata,
    )
    report = _create_report_for_tests(text="", anonymized_text="")
    ctx = ImportContext(
        file_path=source,
        original_path=source,
        center_name="dummy-center",
        file_type="report",
        file_hash="c" * 64,
        current_report=report,
    )

    with caplog.at_level("WARNING"):
        result = anonymizer.anonymize_report(ctx)

    assert reader.output_path == tmp_path / "processed" / f"{report.pdf_hash}.pdf"
    assert result.anonymized_path == reader.output_path
    assert "legacy report tuple contract" in caplog.text


@pytest.mark.django_db
def test_persist_report_anonymization_result_rolls_back_text_fields_on_meta_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base_db_data: bool,
) -> None:
    report = _create_report_for_tests(
        pdf_hash="rollback-" + uuid.uuid4().hex,
        file="rollback-report.pdf",
        text="original text",
        anonymized_text="original anonymized",
    )

    def _fail_to_persist_sensitive_meta(
        *,
        instance: object,
        candidate: object,
    ) -> NoReturn:
        del instance
        del candidate
        raise RuntimeError("Sensitive meta persistence failed")

    monkeypatch.setattr(
        "endoreg_db.import_files.processing.report_processing.report_anonymization.persist_sensitive_meta_candidate",
        _fail_to_persist_sensitive_meta,
    )
    result = ReportAnonymizationResult.model_validate(
        {
            "original_text": "anonymized text",
            "anonymized_text": "anonymized sensitive text",
            "extracted_metadata": {},
            "anonymized_path": tmp_path / "anonymized.pdf",
        }
    )

    with pytest.raises(RuntimeError, match="Sensitive meta persistence failed"):
        persist_report_anonymization_result(report_id=report.pk, result=result)

    report.refresh_from_db()
    assert report.text == "original text"
    assert report.anonymized_text == "original anonymized"


@pytest.mark.django_db
def test_persist_report_anonymization_result_uses_db_freshness(
    base_db_data: bool,
) -> None:
    report = _create_report_for_tests(
        pdf_hash="freshness-" + uuid.uuid4().hex,
        file="freshness-report.pdf",
        text="old-text",
        anonymized_text="old-anon",
    )
    stale_report = RawPdfFile.objects.get(pk=report.pk)
    stale_report.text = "stale-text"
    stale_report.anonymized_text = "stale-anon"

    result = persist_report_anonymization_result(
        report_id=report.pk,
        result=ReportAnonymizationResult.model_validate(
            {
                "original_text": "fresh-text",
                "anonymized_text": "fresh-anon",
                "extracted_metadata": {},
                "anonymized_path": Path("/tmp/ignored.pdf"),
            }
        ),
    )

    db_report = RawPdfFile.objects.get(pk=report.pk)

    assert result.pk == report.pk
    assert result is not stale_report
    assert stale_report.text == "stale-text"
    assert db_report.text == "fresh-text"
    assert db_report.anonymized_text == "fresh-anon"


@pytest.mark.django_db
def test_persist_report_anonymization_keeps_sensitive_meta_stable_on_idempotent_updates(
    tmp_path: Path,
    base_db_data: bool,
) -> None:
    report = _create_report_for_tests(
        pdf_hash="idempotent-" + uuid.uuid4().hex,
        file="idempotent-report.pdf",
        text="old-text",
        anonymized_text="old-anon",
    )
    existing_meta = SensitiveMeta.objects.create(
        center=report.center,
        patient_first_name="idempotent",
    )
    report.sensitive_meta = existing_meta
    report.save(update_fields=["sensitive_meta"])
    start_count = SensitiveMeta.objects.count()
    anonymization_result = ReportAnonymizationResult.model_validate(
        {
            "original_text": "new-text",
            "anonymized_text": "new-anon",
            "extracted_metadata": {"first_name": "idempotent"},
            "anonymized_path": tmp_path / "idempotent-anonymized.pdf",
        }
    )

    first = persist_report_anonymization_result(
        report_id=report.pk,
        result=anonymization_result,
    )
    second = persist_report_anonymization_result(
        report_id=report.pk,
        result=anonymization_result,
    )

    assert first.pk == report.pk
    assert second.pk == report.pk
    assert first.sensitive_meta_id == existing_meta.pk
    assert second.sensitive_meta_id == existing_meta.pk
    assert SensitiveMeta.objects.count() == start_count


@pytest.mark.django_db
def test_persist_sensitive_meta_candidate_returns_sensitive_meta_instance(
    base_db_data: bool,
) -> None:
    report = _create_report_for_tests(
        pdf_hash="candidate-" + uuid.uuid4().hex,
        file="candidate-report.pdf",
    )
    candidate = LxSensitiveMeta.model_validate({"first_name": "A", "last_name": "B"})

    stored_meta = persist_sensitive_meta_candidate(
        instance=report,
        candidate=candidate,
    )

    assert isinstance(stored_meta, SensitiveMeta)
    assert stored_meta.pk is not None
    assert RawPdfFile.objects.get(pk=report.pk).sensitive_meta_id == stored_meta.pk
