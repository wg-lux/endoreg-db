from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import logging

logger = logging.getLogger(__name__)

DEFAULT_MARKOV_CONFIDENCE_THRESHOLD = 0.35
DEFAULT_REPORT_TEMPLATE_MODULE = "report_template_examples"


@dataclass(frozen=True)
class MarkovPriorResult:
    candidate_requirement_set_ids: list[int]
    confidence: float


class RequirementSetLike(Protocol):
    id: int
    name: str
    description: str | None
    requirement_set_type: object | None


@dataclass(frozen=True)
class ReportTemplatePrior:
    tokens: set[str]
    match_score: int


@dataclass(frozen=True)
class ReportTemplateIndex:
    templates: list[Any]
    sections_by_name: dict[str, Any]
    findings_by_name: dict[str, Any]


def _tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    normalized = text.replace("-", " ").replace("_", " ")
    return {part.strip().lower() for part in normalized.split() if part}


def _flatten_history_signal_tokens(history_context: Mapping[str, Any] | None) -> set[str]:
    """
    Extract lightweight lexical signals from report_history context.

    Backward-compatible: unknown shapes are ignored.
    """
    if not history_context:
        return set()

    tokens: set[str] = set()
    previous_examinations = history_context.get("previous_examinations", [])
    if not isinstance(previous_examinations, Sequence) or isinstance(
        previous_examinations, (str, bytes)
    ):
        return tokens

    for exam in previous_examinations:
        if not isinstance(exam, Mapping):
            continue
        tokens |= _tokenize(str(exam.get("examination_name") or ""))
        findings = exam.get("findings", [])
        if not isinstance(findings, Sequence) or isinstance(findings, (str, bytes)):
            continue
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            tokens |= _tokenize(str(finding.get("finding_name") or ""))
            classifications = finding.get("classifications", [])
            if not isinstance(classifications, Sequence) or isinstance(
                classifications, (str, bytes)
            ):
                continue
            for classification in classifications:
                if not isinstance(classification, Mapping):
                    continue
                tokens |= _tokenize(str(classification.get("classification_name") or ""))
                tokens |= _tokenize(
                    str(classification.get("classification_choice_name") or "")
                )

    return tokens


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dtypes_data_root() -> Path:
    return _project_root() / "lx-data-models" / "lx_dtypes" / "data"


@lru_cache(maxsize=1)
def _load_report_templates() -> ReportTemplateIndex:
    data_root = _dtypes_data_root()
    if not data_root.exists():
        logger.debug("lx_dtypes data root not found: %s", data_root)
        return ReportTemplateIndex(templates=[], sections_by_name={}, findings_by_name={})

    try:
        from lx_dtypes.models.interface.DataLoader import DataLoader
    except Exception as exc:
        logger.debug("Failed importing lx_dtypes DataLoader: %s", exc)
        return ReportTemplateIndex(templates=[], sections_by_name={}, findings_by_name={})

    try:
        loader = DataLoader(input_dirs=[data_root])
        loader.load_module_configs()
        kb = loader.load_knowledge_base(DEFAULT_REPORT_TEMPLATE_MODULE)
        return ReportTemplateIndex(
            templates=list(kb.report_template.values()),
            sections_by_name=dict(kb.report_template_section),
            findings_by_name=dict(kb.report_finding),
        )
    except Exception as exc:
        logger.debug("Failed loading report templates from lx_dtypes: %s", exc)
        return ReportTemplateIndex(templates=[], sections_by_name={}, findings_by_name={})


def _as_template_prior_from_graph(*, graph: Any, signal_tokens: set[str]) -> ReportTemplatePrior:
    nodes = list(getattr(graph, "nodes", []) or [])
    edges = list(getattr(graph, "edges", []) or [])

    node_tokens: dict[str, set[str]] = {}
    for node in nodes:
        node_id = getattr(node, "node_id", None)
        raw_tokens = getattr(node, "tokens", [])
        if not isinstance(node_id, str):
            continue
        node_tokens[node_id] = {str(t).lower() for t in raw_tokens if isinstance(t, str)}

    outgoing: dict[str, list[tuple[str, float]]] = {}
    for edge in edges:
        source = getattr(edge, "source_node_id", None)
        target = getattr(edge, "target_node_id", None)
        weight = getattr(edge, "weight", 1.0)
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        try:
            edge_weight = float(weight)
        except Exception:
            edge_weight = 1.0
        outgoing.setdefault(source, []).append((target, max(0.0, edge_weight)))

    matched_nodes: list[str] = []
    favored_tokens: set[str] = set()
    weighted_score = 0.0

    for node_id, tokens in node_tokens.items():
        overlap = tokens & signal_tokens
        if not overlap:
            continue
        matched_nodes.append(node_id)
        favored_tokens |= tokens
        weighted_score += float(len(overlap))

    for node_id in matched_nodes:
        for target_node_id, edge_weight in outgoing.get(node_id, []):
            favored_tokens |= node_tokens.get(target_node_id, set())
            weighted_score += edge_weight

    match_score = int(round(weighted_score))
    return ReportTemplatePrior(tokens=favored_tokens, match_score=max(0, match_score))


def _load_report_template_priors(
    *,
    patient_finding_names: Sequence[str],
    examination_name: str | None,
    history_context: Mapping[str, Any] | None = None,
    history_tokens: Sequence[str] | None = None,
) -> list[ReportTemplatePrior]:
    index = _load_report_templates()
    if not index.templates:
        return []

    try:
        from lx_dtypes.models.knowledge_base.report_template import (
            validate_report_template_structure,
        )
    except Exception as exc:
        logger.debug("Failed importing report template graph validator: %s", exc)
        return []

    examination_tokens = _tokenize(examination_name)
    signal_tokens: set[str] = set(examination_tokens)
    for finding_name in patient_finding_names:
        signal_tokens |= _tokenize(finding_name)
    signal_tokens |= _flatten_history_signal_tokens(history_context)
    for token in history_tokens or []:
        signal_tokens |= _tokenize(token)

    priors: list[ReportTemplatePrior] = []
    for template in index.templates:
        template_examination = getattr(template, "examination", None)
        template_examination_tokens = _tokenize(
            str(template_examination) if template_examination is not None else None
        )
        if examination_tokens and template_examination_tokens:
            if template_examination_tokens != examination_tokens:
                continue

        validation_result = validate_report_template_structure(
            template,
            sections=index.sections_by_name,
            report_findings=index.findings_by_name,
            findings={},
        )
        if not validation_result.ok:
            logger.debug(
                "Skipping invalid report template '%s' for priors",
                getattr(template, "name", "<unknown>"),
            )
            continue

        prior = _as_template_prior_from_graph(
            graph=validation_result.graph,
            signal_tokens=signal_tokens,
        )
        if prior.tokens:
            priors.append(prior)

    return priors


def _score_requirement_set(rs: RequirementSetLike, favored_states: set[str]) -> int:
    tokens = _tokenize(rs.name) | _tokenize(rs.description)
    if rs.requirement_set_type is not None:
        tokens |= _tokenize(getattr(rs.requirement_set_type, "name", None))
    if not tokens:
        return 0
    return len(tokens & favored_states)


def _propose_from_template_priors(
    *,
    patient_finding_names: Sequence[str],
    examination_name: str | None,
    requirement_sets: Sequence[RequirementSetLike],
    history_context: Mapping[str, Any] | None = None,
    history_tokens: Sequence[str] | None = None,
) -> MarkovPriorResult | None:
    priors = _load_report_template_priors(
        patient_finding_names=patient_finding_names,
        examination_name=examination_name,
        history_context=history_context,
        history_tokens=history_tokens,
    )
    if not priors:
        return None

    strongest = max(priors, key=lambda p: p.match_score)
    favored_states = strongest.tokens
    scored = [(rs.id, _score_requirement_set(rs, favored_states)) for rs in requirement_sets]
    positive = [(rs_id, score) for rs_id, score in scored if score > 0]
    if not positive:
        return MarkovPriorResult(candidate_requirement_set_ids=[], confidence=0.2)

    positive.sort(key=lambda item: item[1], reverse=True)
    candidate_ids = [rs_id for rs_id, _ in positive]
    max_score = positive[0][1]
    confidence = min(0.9, 0.3 + 0.08 * float(max_score) + 0.06 * strongest.match_score)
    return MarkovPriorResult(candidate_requirement_set_ids=candidate_ids, confidence=confidence)


def propose_candidate_requirement_sets(
    *,
    patient_finding_names: Sequence[str],
    examination_name: str | None,
    requirement_sets: Sequence[RequirementSetLike],
    history_context: Mapping[str, Any] | None = None,
    history_tokens: Sequence[str] | None = None,
) -> MarkovPriorResult:
    """
    Propose candidate requirement-set IDs using report-template graph priors.

    Priors are assistive only. Callers must apply strict fallback when confidence
    is below threshold.
    """
    if not requirement_sets:
        return MarkovPriorResult(candidate_requirement_set_ids=[], confidence=0.0)

    template_result = _propose_from_template_priors(
        patient_finding_names=patient_finding_names,
        examination_name=examination_name,
        requirement_sets=requirement_sets,
        history_context=history_context,
        history_tokens=history_tokens,
    )
    return (
        template_result
        if template_result is not None
        else MarkovPriorResult(candidate_requirement_set_ids=[], confidence=0.0)
    )
