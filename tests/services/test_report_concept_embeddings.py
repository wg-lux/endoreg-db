from io import StringIO
from typing import Literal, cast
from math import isclose
from pathlib import Path
from thinc.types import Floats1d

import numpy as np
import pytest
import spacy
from django.core.management import call_command
from pydantic import ValidationError
from lx_dtypes.models.contracts.text_embedding import (
    EmbeddingSpace,
    ReportConceptSpan,
    TextEmbedding,
)

from endoreg_db.models import (
    Center,
    Patient,
    Examination,
    PatientExamination,
    Finding,
    PatientFinding,
    FindingClassification,
    FindingClassificationChoice,
    PatientFindingClassification,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.report.concept_embedding import ReportConceptEmbedding
from endoreg_db.utils.paths import get_report_embedding_model_path
from endoreg_db.services.reports.concept_embeddings import (
    SpacyReportTextEmbedder,
    anonymized_report_hash,
    capture_report_concept_embedding,
    default_report_text_embedder,
    similarity_rows,
)


class RecordingEmbedder(SpacyReportTextEmbedder):
    def __init__(self) -> None:
        nlp = spacy.blank("de")
        nlp.vocab.set_vector(
            "Polyp", cast(Floats1d, np.array([1.0, 0.0], dtype="float32"))
        )
        nlp.vocab.set_vector(
            "Wucherung", cast(Floats1d, np.array([0.8, 0.2], dtype="float32"))
        )
        super().__init__(nlp, model="test-local-vectors", revision="1")
        self.inputs: list[str] = []

    def embed(self, anonymized_text: str) -> list[float]:
        self.inputs.append(anonymized_text)
        return list(super().embed(anonymized_text))


@pytest.fixture
def report() -> RawPdfFile:
    center = Center.objects.create(name="embedding-test", center_key="embedding-test")
    patient = Patient.objects.create(patient_hash="embedding-test", center=center)
    examination = PatientExamination.objects.create(
        patient=patient,
        examination=Examination.objects.create(name="colonoscopy"),
        hash="embedding-exam",
    )
    return RawPdfFile.objects.create(
        pdf_hash="embedding-report",
        center=center,
        examination=examination,
        text="RAW_ONLY_MUST_NOT_BE_EMBEDDED",
        anonymized_text="Polyp Wucherung",
        state=RawPdfState.objects.create(anonymized=True, anonymization_validated=True),
    )


def span(
    report: RawPdfFile,
    kind: Literal["examination", "finding", "classification"] = "examination",
) -> ReportConceptSpan:
    assert report.examination is not None
    target = report.examination.pk
    if kind != "examination":
        finding = PatientFinding.objects.create(
            patient_examination=report.examination,
            finding=Finding.objects.create(name="colon_polyp"),
        )
        target = finding.pk
        if kind == "classification":
            classification = FindingClassification.objects.create(name="size")
            choice = FindingClassificationChoice.objects.create(name="small")
            classification.choices.add(choice)
            target = PatientFindingClassification.objects.create(
                finding=finding,
                classification=classification,
                classification_choice=choice,
            ).pk
    return ReportConceptSpan(
        report_id=report.pk,
        source_hash=anonymized_report_hash(report),
        start=0,
        end=5,
        concept_kind=kind,
        annotation_id=target,
    )


@pytest.mark.parametrize("kind", ["examination", "finding", "classification"])
def test_persisted_annotation_vectors_reload_and_retry(
    report: RawPdfFile, kind: Literal["examination", "finding", "classification"]
) -> None:
    request = span(report, kind)
    provider = RecordingEmbedder()
    scope = RawPdfFile.objects.filter(center=report.center)
    saved = capture_report_concept_embedding(
        request, report_scope=scope, embedder=provider
    )
    repeated = capture_report_concept_embedding(
        request, report_scope=scope, embedder=provider
    )
    assert saved.pk == repeated.pk
    assert provider.inputs == ["Polyp"]
    saved.refresh_from_db()
    assert TextEmbedding.model_validate(saved.embedding).vector == [1.0, 0.0]
    assert saved.phrase == "Polyp"
    assert saved.source_hash == request.source_hash
    assert getattr(saved, f"{kind}_annotation_id") == request.annotation_id
    assert (
        saved.concept_name
        == {
            "examination": "colonoscopy",
            "finding": "colon_polyp",
            "classification": "size",
        }[kind]
    )


@pytest.mark.parametrize(
    "invalid",
    ["raw_only", "unvalidated", "stale", "bounds", "scope", "foreign_annotation"],
)
def test_reject_before_embedding(report: RawPdfFile, invalid: str) -> None:
    request = span(report)
    scope = RawPdfFile.objects.filter(pk=report.pk)
    if invalid == "raw_only":
        RawPdfFile.objects.filter(pk=report.pk).update(anonymized_text="")
    elif invalid == "unvalidated":
        RawPdfState.objects.filter(pk=report.state_id).update(
            anonymization_validated=False
        )
    elif invalid == "stale":
        RawPdfFile.objects.filter(pk=report.pk).update(anonymized_text="Changed")
    elif invalid == "bounds":
        request = request.model_copy(update={"end": 100})
    elif invalid == "scope":
        scope = RawPdfFile.objects.none()
    else:
        assert report.examination is not None
        other = PatientExamination.objects.create(
            patient=report.examination.patient,
            examination=report.examination.examination,
            hash="foreign-exam",
        )
        request = request.model_copy(update={"annotation_id": other.pk})
    provider = RecordingEmbedder()
    with pytest.raises((ValueError, RawPdfFile.DoesNotExist)):
        capture_report_concept_embedding(request, report_scope=scope, embedder=provider)
    assert provider.inputs == []
    assert not ReportConceptEmbedding.objects.exists()


def test_similarity_export_preserves_evidence_and_rejects_stale_source(
    report: RawPdfFile,
) -> None:
    request = span(report)
    provider = RecordingEmbedder()
    scope = RawPdfFile.objects.filter(pk=report.pk)
    first = capture_report_concept_embedding(
        request, report_scope=scope, embedder=provider
    )
    second = capture_report_concept_embedding(
        request.model_copy(update={"start": 6, "end": 15}),
        report_scope=scope,
        embedder=provider,
    )
    rows = list(similarity_rows(ReportConceptEmbedding.objects.all(), top_k=1))
    assert len(rows) == 2
    assert rows[0]["source_id"] == first.pk and rows[0]["candidate_id"] == second.pk
    score = rows[0]["cosine_similarity"]
    assert isinstance(score, float) and isclose(score, 0.9701425, rel_tol=1e-6)
    assert (
        rows[0]["source_phrase"] == "Polyp"
        and rows[0]["candidate_phrase"] == "Wucherung"
    )
    assert rows[0]["source_concept"] == "colonoscopy"
    output = StringIO()
    call_command(
        "report_concept_embeddings", "export", center_id=report.center_id, stdout=output
    )
    assert "RAW_ONLY" not in output.getvalue()
    assert "cosine_similarity" in output.getvalue()
    provider.space = provider.space.model_copy(update={"revision": "2"})
    capture_report_concept_embedding(request, report_scope=scope, embedder=provider)
    assert len(list(similarity_rows(ReportConceptEmbedding.objects.all()))) == 2
    RawPdfFile.objects.filter(pk=report.pk).update(anonymized_text="Changed")
    with pytest.raises(ValueError, match="changed"):
        list(similarity_rows(ReportConceptEmbedding.objects.all()))


def test_deleting_source_removes_derived_vectors(report: RawPdfFile) -> None:
    capture_report_concept_embedding(
        span(report),
        report_scope=RawPdfFile.objects.all(),
        embedder=RecordingEmbedder(),
    )
    report.delete()
    assert not ReportConceptEmbedding.objects.exists()


@pytest.mark.no_db
@pytest.mark.parametrize(
    "vector", [[0.0, 0.0], [1.0], [float("nan"), 1.0], [float("inf"), 1.0]]
)
def test_vector_contract_rejects_invalid_values(vector: list[float]) -> None:
    with pytest.raises(ValidationError):
        TextEmbedding(
            space=EmbeddingSpace(
                model="test", revision="1", dimensions=2, preprocessing="none"
            ),
            vector=vector,
        )


def test_stored_vectors_are_immutable(report: RawPdfFile) -> None:
    from django.core.exceptions import ValidationError as DjangoValidationError

    record = capture_report_concept_embedding(
        span(report),
        report_scope=RawPdfFile.objects.all(),
        embedder=RecordingEmbedder(),
    )
    with pytest.raises(DjangoValidationError, match="immutable"):
        record.save()


@pytest.mark.django_db(transaction=True)
def test_concurrent_retries_create_one_vector(report: RawPdfFile) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections

    request, provider = span(report), RecordingEmbedder()

    def capture() -> int:
        close_old_connections()
        try:
            result = capture_report_concept_embedding(
                request,
                report_scope=RawPdfFile.objects.filter(pk=report.pk),
                embedder=provider,
            )
            return int(result.pk)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(capture) for _ in range(2)]
        results = [future.result() for future in futures]
    assert results[0] == results[1]
    assert provider.inputs == ["Polyp"]
    assert ReportConceptEmbedding.objects.count() == 1


def test_default_embedder_is_the_pinned_large_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_load = spacy.load
    loaded: list[Path] = []

    def load_bundled(path: Path) -> spacy.language.Language:
        assert path == get_report_embedding_model_path()
        loaded.append(path)
        return original_load(path)

    default_report_text_embedder.cache_clear()
    monkeypatch.setattr(spacy, "load", load_bundled)
    embedder = default_report_text_embedder()
    assert embedder is default_report_text_embedder()  # loaded once per process
    assert loaded == [get_report_embedding_model_path()]
    assert embedder.space.model_dump() == {
        "model": "de_core_news_lg",
        "revision": "3.8.0",
        "dimensions": 300,
        "preprocessing": "spacy-doc-vector-v1",
    }
    vector = TextEmbedding(space=embedder.space, vector=list(embedder.embed("Polyp")))
    assert len(vector.vector) == 300


def test_provisioned_model_inventory() -> None:
    import yaml
    from endoreg_db.utils.hashs import get_file_hash

    root = get_report_embedding_model_path()
    manifest_path = (
        Path(__file__).resolve().parents[2] / "endoreg_db/data/spacy/manifest.yml"
    )
    manifest = yaml.safe_load(manifest_path.read_text())
    expected = manifest["files_sha256"]
    assert {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    } == set(expected)
    assert {"LICENSE", "LICENSES_SOURCES", "config.cfg", "vocab/vectors"} <= set(
        expected
    )
    for relative, digest in expected.items():
        assert get_file_hash(root / relative) == digest


@pytest.mark.parametrize("error", [OSError("missing"), ValueError("corrupt")])
def test_missing_or_corrupt_provisioned_model_fails(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fail_load(path: Path) -> spacy.language.Language:
        raise error

    default_report_text_embedder.cache_clear()
    monkeypatch.setattr(spacy, "load", fail_load)
    with pytest.raises(
        RuntimeError, match="missing or corrupt; rebuild the model asset"
    ):
        default_report_text_embedder()


@pytest.mark.parametrize("configured", [None, "", "  ", "relative/model"])
def test_model_path_requires_explicit_absolute_directory(
    monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    if configured is None:
        monkeypatch.delenv("ENDOREG_REPORT_EMBEDDING_MODEL_PATH", raising=False)
    else:
        monkeypatch.setenv("ENDOREG_REPORT_EMBEDDING_MODEL_PATH", configured)
    with pytest.raises(RuntimeError, match="ENDOREG_REPORT_EMBEDDING_MODEL_PATH"):
        get_report_embedding_model_path()


def test_model_path_rejects_missing_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ENDOREG_REPORT_EMBEDDING_MODEL_PATH", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="existing absolute directory"):
        get_report_embedding_model_path()


def test_wrong_provisioned_model_identity_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nlp = default_report_text_embedder().nlp
    default_report_text_embedder.cache_clear()
    monkeypatch.setitem(nlp.meta, "name", "core_news_md")

    def load_wrong_model(path: Path) -> spacy.language.Language:
        return nlp

    monkeypatch.setattr(spacy, "load", load_wrong_model)
    with pytest.raises(RuntimeError, match="does not match"):
        default_report_text_embedder()


def test_capture_command_defaults_to_the_large_model(
    report: RawPdfFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = span(report)
    monkeypatch.setattr("sys.stdin", StringIO(request.model_dump_json()))
    call_command(
        "report_concept_embeddings",
        "capture",
        center_id=report.center_id,
        stdout=StringIO(),
    )
    (record,) = ReportConceptEmbedding.objects.all()
    space = TextEmbedding.model_validate(record.embedding).space
    assert (space.model, space.revision, space.dimensions) == (
        "de_core_news_lg",
        "3.8.0",
        300,
    )
    assert record.phrase == "Polyp"


def test_pipelines_without_static_vectors_are_rejected() -> None:
    from lx_anonymizer.ner.bundled_spacy import load_bundled_german_model

    # The anonymizer's small model has no static vectors and is not an embedder.
    for nlp in (spacy.blank("de"), load_bundled_german_model()):
        with pytest.raises(ValueError, match="no static word vectors"):
            SpacyReportTextEmbedder(nlp, model="no-vectors", revision="1")
