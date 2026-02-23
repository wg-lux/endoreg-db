from __future__ import annotations

from dataclasses import dataclass

import pytest

from endoreg_db.services import markov_prior_service as mps


@dataclass
class _ReqType:
    name: str


@dataclass
class _ReqSet:
    id: int
    name: str
    description: str | None = None
    requirement_set_type: _ReqType | None = None


@dataclass
class _Node:
    node_id: str
    tokens: list[str]


@dataclass
class _Edge:
    source_node_id: str
    target_node_id: str
    weight: float = 1.0


@dataclass
class _Graph:
    nodes: list[_Node]
    edges: list[_Edge]


def test_markov_prior_is_safe_when_templates_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mps,
        "_load_report_templates",
        lambda: mps.ReportTemplateIndex(templates=[], sections_by_name={}, findings_by_name={}),
    )

    result = mps.propose_candidate_requirement_sets(
        patient_finding_names=["polyp"],
        examination_name="colonoscopy",
        requirement_sets=[_ReqSet(id=1, name="polyp detection")],
    )

    assert result.candidate_requirement_set_ids == []
    assert result.confidence == 0.0


def test_markov_prior_scores_and_orders_candidates_from_template_prior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mps,
        "_load_report_template_priors",
        lambda **_: [mps.ReportTemplatePrior(tokens={"polyp", "snare"}, match_score=2)],
    )

    req_sets = [
        _ReqSet(id=10, name="Polyp and snare workflow"),
        _ReqSet(id=11, name="Outside checks"),
        _ReqSet(id=12, name="Unrelated medication"),
    ]
    result = mps.propose_candidate_requirement_sets(
        patient_finding_names=["polyp"],
        examination_name="colonoscopy",
        requirement_sets=req_sets,
    )

    assert result.candidate_requirement_set_ids[0] == 10
    assert 12 not in result.candidate_requirement_set_ids
    assert 0.35 <= result.confidence <= 0.9


def test_markov_prior_returns_low_confidence_when_no_candidate_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mps,
        "_load_report_template_priors",
        lambda **_: [mps.ReportTemplatePrior(tokens={"polyp"}, match_score=1)],
    )

    req_sets = [_ReqSet(id=20, name="liver enzymes"), _ReqSet(id=21, name="kidney labs")]
    result = mps.propose_candidate_requirement_sets(
        patient_finding_names=["polyp"],
        examination_name=None,
        requirement_sets=req_sets,
    )

    assert result.candidate_requirement_set_ids == []
    assert result.confidence == 0.2


def test_graph_prior_includes_next_nodes_weighted_tokens() -> None:
    graph = _Graph(
        nodes=[
            _Node(node_id="section:a", tokens=["polyp"]),
            _Node(node_id="section:b", tokens=["snare"]),
            _Node(node_id="section:c", tokens=["outside"]),
        ],
        edges=[
            _Edge(source_node_id="section:a", target_node_id="section:b", weight=1.0),
            _Edge(source_node_id="section:b", target_node_id="section:c", weight=1.0),
        ],
    )

    prior = mps._as_template_prior_from_graph(graph=graph, signal_tokens={"polyp"})

    assert "polyp" in prior.tokens
    assert "snare" in prior.tokens
    assert prior.match_score >= 2


def test_markov_state_tokenizer_should_treat_underscore_like_whitespace() -> None:
    assert mps._tokenize("low_quality") == {"low", "quality"}


@pytest.mark.xfail(reason="Known weakness: graph expansion is only one hop from matched nodes.")
def test_graph_prior_should_expand_multiple_hops() -> None:
    graph = _Graph(
        nodes=[
            _Node(node_id="section:a", tokens=["polyp"]),
            _Node(node_id="section:b", tokens=["snare"]),
            _Node(node_id="section:c", tokens=["clip"]),
        ],
        edges=[
            _Edge(source_node_id="section:a", target_node_id="section:b", weight=1.0),
            _Edge(source_node_id="section:b", target_node_id="section:c", weight=1.0),
        ],
    )

    prior = mps._as_template_prior_from_graph(graph=graph, signal_tokens={"polyp"})
    assert "clip" in prior.tokens
