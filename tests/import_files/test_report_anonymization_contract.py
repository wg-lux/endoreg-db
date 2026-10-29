from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

import pytest
from lx_dtypes.models import SensitiveMeta as LxSensitiveMeta
from lx_dtypes.models.contracts.report_anonymization import (
    ReportAnonymizationProvenance,
    ReportAnonymizationErrorCode,
    ReportAnonymizationRequest,
    ReportAnonymizationResult,
    ReportArtifactValidation,
    ReportExtractionEvidence,
    ReportExtractionReceipt,
)

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
    persist_report_anonymization_result,
    persist_sensitive_meta_candidate,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.patient_identity.hash_aliases import record_patient_hash_alias
from endoreg_db.utils.hashs import get_identity_salt_fingerprint


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
            extraction_evidence=ReportExtractionEvidence(
                outcome="empty" if self.llm_available else "fallback",
                backend="configured_llm" if self.llm_available else "spacy_regex",
                finding_count=0,
                provider_attempted=self.llm_available,
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
    llm_available: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    source = tmp_path / "sensitive.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    reader = _CanonicalReader(llm_available=llm_available)
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_report_staging_dir", lambda: tmp_path / "processed")

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
        current_report=_create_report_for_tests(
            text="", anonymized_text="", raw_meta={"source": "preserved"}
        ),
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
    assert reader.request.attempt_id.hex == ctx.attempt_id
    assert isinstance(result.current_report, RawPdfFile)
    result.current_report.refresh_from_db()
    assert (result.current_report.raw_meta or {})["source"] == "preserved"
    receipt_payload = (result.current_report.raw_meta or {})[
        "report_extraction_receipt"
    ]
    import json

    receipt = ReportExtractionReceipt.model_validate_json(json.dumps(receipt_payload))
    assert receipt.attempt_id == ctx.attempt_id
    assert receipt.source_sha256 == ctx.file_hash
    assert receipt.extraction_evidence.provider_attempted is llm_available


@pytest.mark.unit
@pytest.mark.django_db
def test_report_anonymizer_fails_when_canonical_method_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    source = tmp_path / "sensitive.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    reader = _ReaderWithoutCanonicalContract()
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_report_staging_dir", lambda: tmp_path / "processed")

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
@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("attempt", "attempt identity"),
        ("missing_evidence", "extraction_evidence"),
        ("contradictory_evidence", "successful actual invocation"),
        ("unrequested_provider", "outside the requested policy"),
        ("source", "source identity"),
        ("size", "size does not match"),
        ("hash", "hash does not match"),
        ("foreign", "outside its attempt"),
        ("traversal", "contains traversal"),
        ("symlink", "contains a symlink"),
        ("parent_symlink", "contains a symlink"),
        ("directory", "regular file"),
        ("invalid_contract", "artifact_size_bytes"),
        ("changed_during_hash", "changed during validation"),
    ],
)
def test_report_anonymizer_rejects_untrusted_result_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    defect: str,
    message: str,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module

    class UntrustedReader(_CanonicalReader):
        def process_report(
            self, request: ReportAnonymizationRequest
        ) -> ReportAnonymizationResult:
            result = super().process_report(request)
            updates: dict[str, object] = {}
            if defect == "missing_evidence":
                updates["extraction_evidence"] = None
            elif defect == "contradictory_evidence":
                updates["extraction_evidence"] = result.extraction_evidence.model_copy(
                    update={"outcome": "empty"}
                )
            elif defect == "unrequested_provider":
                updates["extraction_evidence"] = result.extraction_evidence.model_copy(
                    update={
                        "provider_attempted": True,
                        "provider_failure": ReportAnonymizationErrorCode.PROVIDER_UNAVAILABLE,
                    }
                )
            elif defect == "attempt":
                updates["attempt_id"] = uuid.uuid4()
            elif defect == "source":
                updates["source_sha256"] = "d" * 64
            elif defect == "size":
                updates["artifact_size_bytes"] = result.artifact_size_bytes + 1
            elif defect == "hash":
                updates["artifact_sha256"] = "e" * 64
            elif defect == "foreign":
                foreign = tmp_path / "foreign.pdf"
                foreign.write_bytes(result.artifact_path.read_bytes())
                updates["artifact_path"] = foreign
            elif defect == "traversal":
                updates["artifact_path"] = (
                    request.output_directory
                    / ".."
                    / request.output_directory.name
                    / result.artifact_path.name
                )
            elif defect == "symlink":
                link = request.output_directory / "linked.pdf"
                link.symlink_to(result.artifact_path)
                updates["artifact_path"] = link
            elif defect == "parent_symlink":
                link = request.output_directory / "linked-directory"
                link.symlink_to(request.output_directory, target_is_directory=True)
                updates["artifact_path"] = link / result.artifact_path.name
            elif defect == "directory":
                updates["artifact_path"] = request.output_directory
            elif defect == "invalid_contract":
                # Pydantic model_copy deliberately skips validation. The host
                # must not treat an instance as proof of valid nested values.
                updates["artifact_size_bytes"] = 0
            elif defect == "changed_during_hash":
                expected_hash = result.artifact_sha256

                def changed_hash(path: Path) -> str:
                    replacement = path.with_suffix(".replacement")
                    replacement.write_bytes(path.read_bytes())
                    replacement.replace(path)
                    return expected_hash

                monkeypatch.setattr(module, "get_file_hash", changed_hash)
            else:
                raise AssertionError("Unknown defect")
            return result.model_copy(update=updates)

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.4\nsource\n%%EOF\n")
    report = _create_report_for_tests(text="before", anonymized_text="before-anon")
    reader = UntrustedReader(llm_available=False)
    anonymizer = object.__new__(ReportAnonymizer)
    monkeypatch.setattr(module, "_report_staging_dir", lambda: tmp_path / "processed")

    def make_reader(self: ReportAnonymizer, report: RawPdfFile) -> UntrustedReader:
        return reader

    monkeypatch.setattr(ReportAnonymizer, "_instantiate_report_reader", make_reader)

    def unexpected_persistence(**kwargs: object) -> NoReturn:
        raise AssertionError("Invalid result reached persistence")

    monkeypatch.setattr(
        module, "persist_report_anonymization_result", unexpected_persistence
    )
    ctx = ImportContext(
        file_path=source,
        center_name="dummy-center",
        file_type="report",
        file_hash="b" * 64,
        current_report=report,
    )
    with pytest.raises(ValueError, match=message):
        anonymizer.anonymize_report(ctx)

    assert ctx.original_text is None
    assert ctx.anonymized_text is None
    assert ctx.anonymized_path is None
    report.refresh_from_db()
    assert report.text == "before"
    assert report.anonymized_text == "before-anon"
    assert report.sensitive_meta_id is None


@pytest.mark.django_db
def test_persist_report_anonymization_result_rolls_back_text_fields_on_meta_error(
    monkeypatch: pytest.MonkeyPatch,
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
def test_persist_report_anonymization_result_uses_db_freshness() -> None:
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
def test_persist_report_anonymization_keeps_sensitive_meta_stable_on_idempotent_updates() -> (
    None
):
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
def test_persist_sensitive_meta_candidate_returns_sensitive_meta_instance() -> None:
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
    gender_data: None,
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


@pytest.mark.django_db
@pytest.mark.parametrize("conflict", ["duplicate", "real_person", "center"])
def test_report_pseudonym_resolver_rejects_identity_conflicts(
    gender_data: None,
    conflict: str,
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
    stored_meta = persist_sensitive_meta_candidate(instance=report, candidate=candidate)
    patient = stored_meta.pseudo_patient
    assert patient is not None
    if conflict == "duplicate":
        Patient.objects.create(
            center=patient.center,
            patient_hash=patient.patient_hash,
            is_real_person=False,
        )
        message = "conflicting patients"
    elif conflict == "real_person":
        patient.is_real_person = True
        patient.save(update_fields=["is_real_person"])
        message = "real person"
    else:
        assert report.center is not None and patient.patient_hash is not None
        record_patient_hash_alias(
            patient=patient,
            center=report.center,
            patient_hash=patient.patient_hash,
            salt_fingerprint=get_identity_salt_fingerprint(),
        )
        patient.center = Center.objects.create(name="conflicting-report-center")
        patient.save(update_fields=["center"])
        message = "center ownership is inconsistent"
    resolver = ReportAnonymizer._patient_pseudonym_resolver(report)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match=message):
        resolver(candidate)


@pytest.mark.django_db
def test_report_pseudonym_resolver_does_not_reuse_foreign_center_patient(
    gender_data: None,
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
    expected = resolver(candidate)
    patient_hash = SensitiveMeta(
        center=report.center,
        patient_first_name=candidate.first_name,
        patient_last_name=candidate.last_name,
        patient_dob=candidate.dob,
    ).calculate_patient_hash()
    foreign_center = Center.objects.create(name="foreign-report-center")
    foreign = Patient.objects.create(
        center=foreign_center,
        patient_hash=patient_hash,
        is_real_person=False,
        first_name="Foreign",
        last_name="MustNotLeak",
    )
    before = Patient.objects.count()
    assert resolver(candidate) == expected
    assert resolver(candidate) != (foreign.first_name, foreign.last_name)
    assert Patient.objects.count() == before
    foreign.refresh_from_db()
    assert (foreign.center_id, foreign.first_name, foreign.last_name) == (
        foreign_center.pk,
        "Foreign",
        "MustNotLeak",
    )


@pytest.mark.django_db
def test_report_persistence_failure_uses_cleanable_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.processing.report_processing.report_anonymization as module
    from endoreg_db.import_files.file_storage.cleanup import cleanup_staging_files
    from endoreg_db.utils.paths import get_runtime_paths
    from endoreg_db.utils.file_operations import atomic_write_file

    source = tmp_path / "source.pdf"
    atomic_write_file(destination=source, content=[b"%PDF-1.4\nsource\n%%EOF\n"])
    reader = _CanonicalReader(llm_available=False)
    anonymizer = object.__new__(ReportAnonymizer)

    def make_reader(self: ReportAnonymizer, report: RawPdfFile) -> _CanonicalReader:
        return reader

    def reject_identity(**kwargs: object) -> NoReturn:
        raise ValueError(
            "Configured identity salt does not match legacy patient hashes"
        )

    monkeypatch.setattr(ReportAnonymizer, "_instantiate_report_reader", make_reader)
    monkeypatch.setattr(module, "persist_report_anonymization_result", reject_identity)
    ctx = ImportContext(
        file_path=source,
        center_name="dummy-center",
        file_type="report",
        file_hash="b" * 64,
        current_report=_create_report_for_tests(),
    )
    with pytest.raises(ValueError, match="does not match legacy"):
        anonymizer.anonymize_report(ctx)
    artifact = ctx.anonymized_path
    assert artifact is not None and artifact.exists()
    assert artifact.is_relative_to(get_runtime_paths().import_anonymized_report)
    assert not artifact.is_relative_to(get_runtime_paths().anonym_report)
    cleanup_staging_files((artifact,), label="failed import staging")
    assert not artifact.exists()
    assert source.exists()


@pytest.mark.django_db
def test_persisted_receipt_rejects_invalid_evidence() -> None:
    from endoreg_db.schemas.persisted_json import validate_raw_pdf_meta_payload

    with pytest.raises(ValueError, match="report_extraction_receipt"):
        validate_raw_pdf_meta_payload(
            {"report_extraction_receipt": {"attempt_id": "invalid"}}
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provider_fails,fallback,rollback",
    [
        (False, False, False),
        (True, True, False),
        (True, False, False),
        (False, False, True),
    ],
)
def test_producer_extraction_reaches_native_fenced_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider_fails: bool,
    fallback: bool,
    rollback: bool,
) -> None:
    # Real producer extraction/result validation and host persistence/native reducer;
    # OCR and model calls are isolated deterministic boundaries.
    from lx_anonymizer.report_reader import ReportReader
    from lx_anonymizer import report_contracts
    from lx_anonymizer.sensitive_meta_interface import (
        SensitiveMeta as ProducerSensitiveMeta,
    )

    provider_error = cast(
        type[RuntimeError], getattr(report_contracts, "ProviderUnavailableError")
    )
    from lx_dtypes.models.meta.ReportMeta import (
        ReportProcessRequest,
        ReportProcessResult,
    )
    from lx_anonymizer.report_reader import reader as producer_module
    from endoreg_db.import_files.report_import_service import ReportImportService
    from endoreg_db.services.imports.execution import ImportExecutionFence
    from endoreg_db.services.reports.import_fencing import (
        acquire_report_import_fence,
        report_import_finalization_guard,
        report_import_mutation_guard,
        mark_report_import_fence_failed,
    )
    from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
    import endoreg_db.import_files.processing.report_processing.report_anonymization as host

    reader = object.__new__(ReportReader)
    reader.llm_available = True
    reader.sensitive_meta = ProducerSensitiveMeta()
    monkeypatch.setattr(
        producer_module.settings,
        "METADATA_MODEL_ORDER",
        ["llm", "spacy"] if fallback else ["llm"],
    )

    def should_attempt(text: str) -> bool:
        return True

    monkeypatch.setattr(reader, "_should_attempt_report_llm", should_attempt)

    def provider(text: str) -> dict[str, object]:
        if provider_fails:
            raise provider_error("report metadata provider failed")
        return {}

    monkeypatch.setattr(reader, "extract_report_meta_with_llm", provider)

    def local_extract(text: str, path: Path | None) -> dict[str, object]:
        return {}

    monkeypatch.setattr(reader, "extract_report_meta", local_extract)

    def process(request: ReportProcessRequest) -> ReportProcessResult:
        meta = reader._extract_or_default_report_meta(  # pyright: ignore[reportPrivateUsage]
            text="synthetic report content",
            pdf_path=request.pdf_path,
            use_provider_llm=True,
        )
        output = request.anonymized_pdf_output_path
        assert output is not None
        output = Path(output)
        output.write_bytes(
            ReportImportService._render_single_page_pdf("synthetic report")  # pyright: ignore[reportPrivateUsage]
        )
        return ReportProcessResult(
            text="synthetic report",
            anonymized_text="synthetic report",
            report_meta=meta,
            anonymized_pdf_path=output,
        )

    monkeypatch.setattr(reader, "_process_report_request", process)

    def make_reader(self: ReportAnonymizer, report: RawPdfFile) -> ReportReader:
        return reader

    monkeypatch.setattr(ReportAnonymizer, "_instantiate_report_reader", make_reader)
    monkeypatch.setattr(host, "_report_staging_dir", lambda: tmp_path / "output")

    def persist_meta(
        *, instance: RawPdfFile, candidate: LxSensitiveMeta
    ) -> SensitiveMeta:
        return SensitiveMeta.objects.create(center=instance.center)

    monkeypatch.setattr(host, "persist_sensitive_meta_candidate", persist_meta)
    source = tmp_path / "source.pdf"
    source.write_bytes(ReportImportService._render_single_page_pdf("synthetic source"))  # pyright: ignore[reportPrivateUsage]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = _create_report_for_tests(pdf_hash=digest, raw_meta={"source": "preserved"})
    fence = acquire_report_import_fence(digest)
    ctx = ImportContext(
        file_path=source,
        center_name="test-center",
        file_type="report",
        file_hash=digest,
        current_report=report,
    )
    ctx.bind_execution_fence(
        ImportExecutionFence(
            attempt_id=fence.owner_id.hex,
            guard=lambda: None,
            mutation_guard=lambda: report_import_mutation_guard(fence),
        )
    )
    if provider_fails and not fallback:
        with pytest.raises(provider_error):
            ReportAnonymizer().anonymize_report(ctx)
        assert mark_report_import_fence_failed(fence)
        report.refresh_from_db()
        assert "report_extraction_receipt" not in (report.raw_meta or {})
        assert (
            ReportImportAttempt.objects.get(pk=digest).status
            == ReportImportAttempt.STATUS_FAILED
        )
    elif rollback:
        from django.db import transaction

        with (
            pytest.raises(RuntimeError, match="rollback receipt"),
            transaction.atomic(),
        ):
            ReportAnonymizer().anonymize_report(ctx)
            raise RuntimeError("rollback receipt")
        report.refresh_from_db()
        assert report.raw_meta == {"source": "preserved"}
        assert mark_report_import_fence_failed(fence)
    else:
        ReportAnonymizer().anonymize_report(ctx)
        assert (
            ReportImportAttempt.objects.get(pk=digest).status
            == ReportImportAttempt.STATUS_ACTIVE
        )
        with report_import_finalization_guard(fence):
            pass
        report.refresh_from_db()
        import json

        receipt = ReportExtractionReceipt.model_validate_json(
            json.dumps((report.raw_meta or {})["report_extraction_receipt"])
        )
        assert (report.raw_meta or {})["source"] == "preserved"
        assert receipt.attempt_id == fence.owner_id.hex
        assert receipt.extraction_evidence.outcome == (
            "fallback" if fallback else "empty"
        )
        assert receipt.extraction_evidence.provider_attempted
        assert (
            receipt.extraction_evidence.provider_failure is not None
        ) is provider_fails
        assert (
            ReportImportAttempt.objects.get(pk=digest).status
            == ReportImportAttempt.STATUS_SUCCEEDED
        )
