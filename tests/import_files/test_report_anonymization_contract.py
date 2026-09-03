from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import pytest
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta
from lx_dtypes.models.contracts.report_anonymization import (
    ReportAnonymizationProvenance,
    ReportAnonymizationRequest,
    ReportAnonymizationResult,
    ReportArtifactValidation,
)

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
    persist_report_anonymization_result,
    persist_sensitive_meta_candidate,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta


class _CanonicalReader:
    request: ReportAnonymizationRequest | None = None

    def __init__(self, *, llm_available: bool) -> None:
        self.llm_available = llm_available

    def process_report(
        self, request: ReportAnonymizationRequest
    ) -> ReportAnonymizationResult:
        self.request = request
        output_directory = getattr(request, "output_directory")
        attempt_id = getattr(request, "attempt_id")
        assert isinstance(output_directory, Path)
        artifact_path = output_directory / f"{attempt_id}.pdf"
        artifact_path.write_bytes(b"%PDF-1.4\nanonymized\n%%EOF\n")
        artifact_bytes = artifact_path.read_bytes()
        return ReportAnonymizationResult(
            attempt_id=request.attempt_id,
            source_sha256=request.source_sha256,
            original_text="original",
            anonymized_text="anonymized",
            extracted_metadata=LxSensitiveMeta(),
            artifact_path=artifact_path,
            artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            artifact_size_bytes=len(artifact_bytes),
            artifact_validation=ReportArtifactValidation(
                page_count=1,
                repaired=False,
            ),
            provenance=ReportAnonymizationProvenance(
                anonymizer_version="test",
                used_llm=self.llm_available,
                deterministic=not self.llm_available,
            ),
        )


class _ReaderWithoutCanonicalContract:
    llm_available = False


@dataclass(frozen=True)
class _PersistenceResult:
    original_text: str
    anonymized_text: str
    extracted_metadata: LxSensitiveMeta


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


def _persistence_result(
    *,
    original_text: str,
    anonymized_text: str,
    extracted_metadata: dict[str, object] | None = None,
) -> _PersistenceResult:
    return _PersistenceResult(
        original_text=original_text,
        anonymized_text=anonymized_text,
        extracted_metadata=LxSensitiveMeta.from_dict(extracted_metadata),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("llm_available", [False, True])
def test_report_anonymizer_uses_canonical_attempt_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base_db_data: bool,
    llm_available: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    source = tmp_path / "sensitive.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    reader = _CanonicalReader(llm_available=llm_available)
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_processed_report_dir", lambda: tmp_path / "processed")

    def fake_reader(
        self: ReportAnonymizer,
        report: RawPdfFile,
    ) -> _CanonicalReader:
        del report
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

    with caplog.at_level(
        "INFO",
        logger=(
            "endoreg_db.import_files.processing.report_processing.report_anonymization"
        ),
    ):
        result = anonymizer.anonymize_report(ctx)

    assert reader.request is not None
    assert getattr(reader.request, "source_path") == source
    assert getattr(reader.request, "source_sha256") == "b" * 64
    assert getattr(reader.request, "options").use_llm is llm_available
    expected_event = (
        "report_anonymization.llm_ready_selected"
        if llm_available
        else "report_anonymization.spacy_fallback_selected"
    )
    event = next(
        getattr(record, "structured_event", {})
        for record in caplog.records
        if getattr(record, "structured_event", {}).get("event") == expected_event
    )
    assert event["llm_available"] is llm_available
    output_directory = getattr(reader.request, "output_directory")
    assert isinstance(output_directory, Path)
    assert output_directory.parent == tmp_path / "processed"
    assert result.anonymized_path is not None
    assert result.anonymized_path.parent == output_directory


@pytest.mark.unit
@pytest.mark.django_db
def test_report_anonymizer_fails_when_canonical_method_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base_db_data: bool,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    source = tmp_path / "sensitive.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    reader = _ReaderWithoutCanonicalContract()
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_processed_report_dir", lambda: tmp_path / "processed")

    def fake_reader(
        self: ReportAnonymizer,
        report: RawPdfFile,
    ) -> _ReaderWithoutCanonicalContract:
        del report
        return reader

    monkeypatch.setattr(ReportAnonymizer, "_instantiate_report_reader", fake_reader)
    ctx = ImportContext(
        file_path=source,
        original_path=source,
        center_name="dummy-center",
        file_type="report",
        file_hash="c" * 64,
        current_report=_create_report_for_tests(text="", anonymized_text=""),
    )

    with pytest.raises(AttributeError, match="process_report"):
        anonymizer.anonymize_report(ctx)


@pytest.mark.django_db
def test_persist_report_anonymization_result_rolls_back_text_fields_on_meta_error(
    monkeypatch: pytest.MonkeyPatch,
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
    result = _persistence_result(
        original_text="anonymized text",
        anonymized_text="anonymized sensitive text",
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
        result=_persistence_result(
            original_text="fresh-text",
            anonymized_text="fresh-anon",
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
    anonymization_result = _persistence_result(
        original_text="new-text",
        anonymized_text="new-anon",
        extracted_metadata={"first_name": "idempotent"},
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


@pytest.mark.django_db
def test_report_pseudonym_resolver_reuses_existing_patient_name(
    base_db_data: bool,
) -> None:
    report = _create_report_for_tests()
    candidate = LxSensitiveMeta.model_validate(
        {
            "first_name": "Max",
            "last_name": "Muster",
            "dob": "1980-02-03",
            "gender": "male",
        }
    )
    resolver = ReportAnonymizer._patient_pseudonym_resolver(report)  # pyright: ignore[reportPrivateUsage]
    first_name, last_name = resolver(candidate)

    stored_meta = persist_sensitive_meta_candidate(
        instance=report,
        candidate=candidate,
    )

    assert stored_meta.pseudo_patient is not None
    assert (first_name, last_name) == (
        stored_meta.pseudo_patient.first_name,
        stored_meta.pseudo_patient.last_name,
    )

    stored_meta.pseudo_patient.first_name = "Legacy"
    stored_meta.pseudo_patient.last_name = "Established"
    stored_meta.pseudo_patient.save(update_fields=["first_name", "last_name"])
    assert resolver(candidate) == ("Legacy", "Established")
