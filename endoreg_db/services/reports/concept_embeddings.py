"""Explicit span-to-annotation learning from validated anonymized report text only."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from functools import cache
from heapq import nlargest
from typing import Protocol, TYPE_CHECKING, Literal

from django.db import transaction
from django.db.models import QuerySet
from lx_dtypes.models.contracts.text_embedding import (
    EmbeddingSpace,
    ReportConceptSpan,
    TextEmbedding,
)

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.models.report.concept_embedding import ReportConceptEmbedding
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.utils.hashs import get_bytes_hash

if TYPE_CHECKING:
    from spacy.language import Language


class ReportTextEmbedder(Protocol):
    space: EmbeddingSpace

    def embed(self, anonymized_text: str) -> Sequence[float]: ...


class SpacyReportTextEmbedder:
    """Average static word vectors of an explicitly loaded local spaCy pipeline.

    No downloads or remote inference. Pipelines without static vectors (such as the
    ``*_sm`` models) are rejected.
    """

    def __init__(self, nlp: Language, *, model: str, revision: str) -> None:
        if nlp.vocab.vectors_length <= 0:
            raise ValueError(
                "spaCy pipeline has no static word vectors; it cannot embed report text"
            )
        self.nlp = nlp
        self.space = EmbeddingSpace(
            model=model,
            revision=revision,
            dimensions=nlp.vocab.vectors_length,
            preprocessing="spacy-doc-vector-v1",
        )

    def embed(self, anonymized_text: str) -> Sequence[float]:
        return [float(value) for value in self.nlp(anonymized_text).vector]


DEFAULT_EMBEDDING_MODEL = "de_core_news_lg"
DEFAULT_EMBEDDING_MODEL_VERSION = "3.8.0"
_DEFAULT_EMBEDDING_DIMENSIONS = 300


@cache
def default_report_text_embedder() -> SpacyReportTextEmbedder:
    """Load the pinned ``de_core_news_lg`` dependency once per process.

    The pipeline is checked against its model contract, as lx-anonymizer checks its
    bundled model, so a mismatched installation fails instead of starting a new space.
    """
    import spacy

    try:
        nlp = spacy.load(DEFAULT_EMBEDDING_MODEL)
    except OSError as exc:
        raise RuntimeError(
            f"{DEFAULT_EMBEDDING_MODEL} is not installed; reinstall EndoReg with its "
            "locked dependencies (uv sync)"
        ) from exc
    if (
        nlp.lang != "de"
        or nlp.meta.get("version") != DEFAULT_EMBEDDING_MODEL_VERSION
        or nlp.vocab.vectors_length != _DEFAULT_EMBEDDING_DIMENSIONS
    ):
        raise RuntimeError(
            f"Installed {DEFAULT_EMBEDDING_MODEL} does not match its "
            f"{DEFAULT_EMBEDDING_MODEL_VERSION} embedding contract"
        )
    return SpacyReportTextEmbedder(
        nlp, model=DEFAULT_EMBEDDING_MODEL, revision=DEFAULT_EMBEDDING_MODEL_VERSION
    )


def _digest(value: object) -> str:
    return get_bytes_hash(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    )


def _text(report: RawPdfFile) -> str:
    if (
        report.state is None
        or not report.state.anonymized
        or not report.state.anonymization_validated
    ):
        raise ValueError("Report anonymization must be validated before embedding")
    text = report.anonymized_text
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Persisted anonymized report text is required")
    return text


def anonymized_report_hash(report: RawPdfFile) -> str:
    return get_bytes_hash(_text(report).encode("utf-8"))


def _target(
    record: ReportConceptEmbedding,
) -> tuple[
    Literal["examination", "finding", "classification"],
    int,
    PatientExamination,
    str,
    str,
]:
    selected = [
        record.examination_annotation_id is not None,
        record.finding_annotation_id is not None,
        record.classification_annotation_id is not None,
    ]
    if sum(selected) != 1:
        raise ValueError("Exactly one persisted clinical annotation is required")
    if record.examination_annotation is not None:
        examination = record.examination_annotation
        if examination.examination is None:
            raise ValueError("Examination annotation has no concept")
        return (
            "examination",
            examination.pk,
            examination,
            examination.examination.name,
            "",
        )
    if record.finding_annotation is not None:
        finding = record.finding_annotation
        if not finding.is_active:
            raise ValueError("Finding annotation is inactive")
        return (
            "finding",
            finding.pk,
            finding.patient_examination,
            finding.finding.name,
            "",
        )
    classification = record.classification_annotation
    if (
        classification is None
        or not classification.is_active
        or not classification.finding.is_active
    ):
        raise ValueError("Classification annotation is inactive")
    return (
        "classification",
        classification.pk,
        classification.finding.patient_examination,
        classification.classification.name,
        classification.classification_choice.name,
    )


def validate_record_source(record: ReportConceptEmbedding) -> None:
    """Reject stale text, reassigned annotations and forged source metadata."""
    text = _text(record.report)
    if anonymized_report_hash(record.report) != record.source_hash:
        raise ValueError("Anonymized report text changed")
    if (
        not 0 <= record.start < record.end <= len(text)
        or record.end - record.start > 2000
    ):
        raise ValueError("Report span is out of bounds")
    if record.phrase != text[record.start : record.end] or not record.phrase.strip():
        raise ValueError("Phrase must equal the persisted anonymized report span")
    kind, annotation_id, examination, name, choice = _target(record)
    if record.report.examination_id != examination.pk:
        raise ValueError("Annotation belongs to a different report examination")
    if (
        record.report.center_id is None
        or examination.patient.center_id != record.report.center_id
    ):
        raise ValueError("Report and annotation must belong to the same center")
    if (
        record.concept_name,
        record.choice_name,
        record.knowledge_base_module,
        record.knowledge_base_version,
    ) != (
        name,
        choice,
        examination.knowledge_base_module,
        examination.knowledge_base_version,
    ):
        raise ValueError("Annotated concept changed since embedding capture")
    vector = TextEmbedding.model_validate(record.embedding)
    request = ReportConceptSpan(
        report_id=record.report_id,
        source_hash=record.source_hash,
        start=record.start,
        end=record.end,
        concept_kind=kind,
        annotation_id=annotation_id,
    )
    if record.space_key != _digest(
        vector.space.model_dump()
    ) or record.identity != _digest(
        {"span": request.model_dump(), "space": vector.space.model_dump()}
    ):
        raise ValueError("Embedding identity does not match its source and space")


def capture_report_concept_embedding(
    request: ReportConceptSpan,
    *,
    report_scope: QuerySet[RawPdfFile],
    embedder: ReportTextEmbedder,
) -> ReportConceptEmbedding:
    """Explicitly align an existing clinical annotation with an anonymized span.

    The caller supplies its authorized report scope. No arbitrary text or externally
    calculated vectors are accepted. Local inference runs after source checks and
    under the report row lock; repeats return the existing vector without inference.
    """
    request = ReportConceptSpan.model_validate(request.model_dump())
    space = EmbeddingSpace.model_validate(embedder.space.model_dump())
    database = report_scope.db
    with transaction.atomic(using=database):
        report = (
            report_scope.select_for_update(of=("self",))
            .select_related("state")
            .get(pk=request.report_id)
        )
        if report.state_id is not None:
            report.state = (
                RawPdfState.objects.using(database)
                .select_for_update()
                .get(pk=report.state_id)
            )
        text = _text(report)
        if anonymized_report_hash(report) != request.source_hash:
            raise ValueError("Anonymized report text changed")
        if request.end > len(text):
            raise ValueError("Report span is out of bounds")
        phrase = text[request.start : request.end]
        if not phrase.strip():
            raise ValueError("Empty report phrase")
        identity = _digest({"span": request.model_dump(), "space": space.model_dump()})
        existing = (
            ReportConceptEmbedding.objects.using(database)
            .filter(identity=identity)
            .first()
        )
        if existing is not None:
            validate_record_source(existing)
            return existing
        if report.examination_id is None:
            raise ValueError("Report must have a persisted examination")
        PatientExamination.objects.using(database).select_for_update().get(
            pk=report.examination_id
        )
        record = ReportConceptEmbedding(
            report=report,
            identity=identity,
            source_hash=request.source_hash,
            start=request.start,
            end=request.end,
            phrase=phrase,
            space_key=_digest(space.model_dump()),
        )
        if request.concept_kind == "examination":
            record.examination_annotation = (
                PatientExamination.objects.using(database)
                .select_for_update()
                .get(pk=request.annotation_id)
            )
        elif request.concept_kind == "finding":
            record.finding_annotation = (
                PatientFinding.objects.using(database)
                .select_for_update()
                .get(pk=request.annotation_id)
            )
        else:
            record.classification_annotation = (
                PatientFindingClassification.objects.using(database)
                .select_for_update()
                .get(pk=request.annotation_id)
            )
        if record.classification_annotation is not None:
            record.classification_annotation.finding = (
                PatientFinding.objects.using(database)
                .select_for_update()
                .get(pk=record.classification_annotation.finding.pk)
            )
        _, _, examination, name, choice = _target(record)
        if (
            report.examination_id != examination.pk
            or report.center_id != examination.patient.center_id
            or report.center_id is None
        ):
            raise ValueError(
                "Annotation must belong to this report's examination and center"
            )
        record.concept_name, record.choice_name = name, choice
        record.knowledge_base_module = examination.knowledge_base_module
        record.knowledge_base_version = examination.knowledge_base_version
        vector = TextEmbedding(space=space, vector=list(embedder.embed(phrase)))
        record.embedding = vector.model_dump(mode="json")
        record.save(using=database)
        return record


SIMILARITY_COLUMNS = [
    "source_id",
    "candidate_id",
    "source_report_id",
    "candidate_report_id",
    "source_phrase",
    "candidate_phrase",
    "source_hash",
    "candidate_hash",
    "source_start",
    "source_end",
    "candidate_start",
    "candidate_end",
    "source_kind",
    "candidate_kind",
    "source_annotation_id",
    "candidate_annotation_id",
    "source_concept",
    "candidate_concept",
    "source_choice",
    "candidate_choice",
    "knowledge_base_module",
    "knowledge_base_version",
    "model",
    "revision",
    "preprocessing",
    "dimensions",
    "cosine_similarity",
]


def similarity_rows(
    records: QuerySet[ReportConceptEmbedding], *, top_k: int = 20
) -> Iterator[dict[str, str | int | float]]:
    """Bounded exact comparison of scoped current annotated spans, never auto-approved synonyms."""
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100")
    selected = list(
        records.select_related(
            "report__state",
            "examination_annotation",
            "finding_annotation",
            "classification_annotation",
        ).order_by("pk")[:1001]
    )
    if len(selected) > 1000:
        raise ValueError("Select at most 1000 embeddings per similarity export")
    vectors: dict[int, TextEmbedding] = {}
    targets: dict[
        int,
        tuple[
            Literal["examination", "finding", "classification"],
            int,
            PatientExamination,
            str,
            str,
        ],
    ] = {}
    for record in selected:
        validate_record_source(record)
        vectors[record.pk] = TextEmbedding.model_validate(record.embedding)
        targets[record.pk] = _target(record)
    for source in selected:
        matches = nlargest(
            top_k,
            (
                (
                    vectors[source.pk].cosine(vectors[candidate.pk]),
                    -candidate.pk,
                    candidate,
                )
                for candidate in selected
                if candidate.pk != source.pk
                and candidate.space_key == source.space_key
                and (candidate.knowledge_base_module, candidate.knowledge_base_version)
                == (source.knowledge_base_module, source.knowledge_base_version)
            ),
            key=lambda item: (item[0], item[1]),
        )
        for score, _, candidate in matches:
            space = vectors[source.pk].space
            yield dict(
                zip(
                    SIMILARITY_COLUMNS,
                    [
                        source.pk,
                        candidate.pk,
                        source.report_id,
                        candidate.report_id,
                        source.phrase,
                        candidate.phrase,
                        source.source_hash,
                        candidate.source_hash,
                        source.start,
                        source.end,
                        candidate.start,
                        candidate.end,
                        targets[source.pk][0],
                        targets[candidate.pk][0],
                        targets[source.pk][1],
                        targets[candidate.pk][1],
                        source.concept_name,
                        candidate.concept_name,
                        source.choice_name,
                        candidate.choice_name,
                        source.knowledge_base_module,
                        source.knowledge_base_version,
                        space.model,
                        space.revision,
                        space.preprocessing,
                        space.dimensions,
                        score,
                    ],
                    strict=True,
                )
            )
