from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from typing import Literal, TypeAlias

from endoreg_db.schemas.k_pseudonymity import (
    KPseudonymityAuditManifest,
    KPseudonymityReleaseConfig,
    ReleasePredicateAudit,
)
from endoreg_db.services.k_pseudonymity_predicate import (
    CellValue,
    ClassKey,
    KPseudonymityInputError,
    ReleaseRow,
    canonical_value as _canonical_value,
    categorical_distribution as _categorical_distribution,
    equivalence_classes as _equivalence_classes,
    evaluate_release_predicate,
    jensen_shannon_divergence,
    total_variation_distance,
    wasserstein_1_distance,
)


@dataclass(frozen=True)
class KPseudonymityReleaseResult:
    released_rows: tuple[ReleaseRow, ...] | None
    manifest: KPseudonymityAuditManifest


@dataclass
class _SearchBudget:
    remaining: int
    evaluations: int = 0

    def consume(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        self.evaluations += 1
        return True


_SearchReason: TypeAlias = Literal[
    "bounded_search_found_feasible_release",
    "max_state_evaluations_reached",
    "max_synthetic_rows_reached",
    "no_permitted_unvisited_successor",
    "utility_threshold_exceeded_without_local_privacy_violation",
]
_CandidateRank: TypeAlias = tuple[float, float, str]


@dataclass(frozen=True)
class _SearchState:
    rows: tuple[ReleaseRow, ...]
    audit: ReleasePredicateAudit
    synthetic_indices: tuple[int, ...]


@dataclass(frozen=True)
class _SearchOutcome:
    state: _SearchState
    reason: _SearchReason


@dataclass(frozen=True)
class _RankedCandidate:
    rank: _CandidateRank
    rows: tuple[ReleaseRow, ...]
    audit: ReleasePredicateAudit


@dataclass(frozen=True)
class _SuccessorSelection:
    candidate: _RankedCandidate | None
    budget_exhausted: bool = False


def build_k_pseudonymous_release(
    rows: Sequence[Mapping[str, object]],
    config: KPseudonymityReleaseConfig,
) -> KPseudonymityReleaseResult:
    """
    Construct a bounded synthetic augmentation and release only a feasible table.

    Real input rows are normalized into a new table and never modified. Synthetic
    provenance is retained only in the protected manifest, not in released rows.
    """

    original_rows = _normalize_rows(rows, config)
    reference_rows = tuple(_copy_row(row) for row in original_rows)
    _validate_sensitive_support(reference_rows, config)

    budget = _SearchBudget(remaining=config.max_state_evaluations)
    if not budget.consume():  # pragma: no cover - schema enforces a positive budget
        raise AssertionError("positive state-evaluation budget required")
    initial_audit = evaluate_release_predicate(
        reference_rows=reference_rows,
        candidate_rows=original_rows,
        config=config,
    )
    initial_state = _SearchState(
        rows=tuple(_copy_row(row) for row in original_rows),
        audit=initial_audit,
        synthetic_indices=(),
    )

    if initial_audit.feasible:
        return _result(
            rows=initial_state.rows,
            config=config,
            initial_audit=initial_audit,
            final_audit=initial_audit,
            synthetic_indices=initial_state.synthetic_indices,
            budget=budget,
            status="released",
            reason="initial_table_satisfies_release_predicate",
        )

    outcome = _run_bounded_search(
        initial_state=initial_state,
        reference_rows=reference_rows,
        config=config,
        budget=budget,
    )
    released = outcome.state.audit.feasible
    return _result(
        rows=outcome.state.rows,
        config=config,
        initial_audit=initial_audit,
        final_audit=outcome.state.audit,
        synthetic_indices=outcome.state.synthetic_indices,
        budget=budget,
        status="released" if released else "no_release",
        reason=outcome.reason,
    )


def _run_bounded_search(
    *,
    initial_state: _SearchState,
    reference_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
    budget: _SearchBudget,
) -> _SearchOutcome:
    state = initial_state
    visited = {_canonical_table(state.rows, config.release_columns)}
    for _ in range(config.max_synthetic_rows):
        target_key = _most_severe_violating_class(
            state.rows,
            reference_rows=reference_rows,
            config=config,
        )
        if target_key is None:
            return _SearchOutcome(
                state=state,
                reason="utility_threshold_exceeded_without_local_privacy_violation",
            )

        selection = _select_best_successor(
            state=state,
            reference_rows=reference_rows,
            target_key=target_key,
            config=config,
            visited=visited,
            budget=budget,
        )
        if selection.budget_exhausted:
            return _SearchOutcome(
                state=state,
                reason="max_state_evaluations_reached",
            )
        candidate = selection.candidate
        if candidate is None:
            return _SearchOutcome(
                state=state,
                reason="no_permitted_unvisited_successor",
            )

        state = _SearchState(
            rows=candidate.rows,
            audit=candidate.audit,
            synthetic_indices=(
                *state.synthetic_indices,
                len(candidate.rows) - 1,
            ),
        )
        if state.audit.feasible:
            return _SearchOutcome(
                state=state,
                reason="bounded_search_found_feasible_release",
            )
    return _SearchOutcome(state=state, reason="max_synthetic_rows_reached")


def _select_best_successor(
    *,
    state: _SearchState,
    reference_rows: Sequence[ReleaseRow],
    target_key: ClassKey,
    config: KPseudonymityReleaseConfig,
    visited: set[tuple[str, ...]],
    budget: _SearchBudget,
) -> _SuccessorSelection:
    current_target_severity = _class_violation_severity(
        _equivalence_classes(state.rows, config.quasi_identifiers)[target_key],
        reference_rows=reference_rows,
        config=config,
    )
    best: _RankedCandidate | None = None
    for synthetic_row in _candidate_synthetic_rows(
        state.rows,
        reference_rows=reference_rows,
        target_key=target_key,
        config=config,
    ):
        selection = _rank_candidate(
            current_rows=state.rows,
            synthetic_row=synthetic_row,
            target_key=target_key,
            current_target_severity=current_target_severity,
            reference_rows=reference_rows,
            config=config,
            visited=visited,
            budget=budget,
        )
        if selection.budget_exhausted:
            return selection
        candidate = selection.candidate
        if candidate is not None and (best is None or candidate.rank < best.rank):
            best = candidate
    return _SuccessorSelection(candidate=best)


def _rank_candidate(
    *,
    current_rows: tuple[ReleaseRow, ...],
    synthetic_row: ReleaseRow,
    target_key: ClassKey,
    current_target_severity: float,
    reference_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
    visited: set[tuple[str, ...]],
    budget: _SearchBudget,
) -> _SuccessorSelection:
    candidate_rows = (*current_rows, synthetic_row)
    candidate_target_severity = _class_violation_severity(
        _equivalence_classes(candidate_rows, config.quasi_identifiers)[target_key],
        reference_rows=reference_rows,
        config=config,
    )
    if candidate_target_severity >= current_target_severity - 1e-12:
        return _SuccessorSelection(candidate=None)

    canonical_state = _canonical_table(candidate_rows, config.release_columns)
    if canonical_state in visited:
        return _SuccessorSelection(candidate=None)
    if not budget.consume():
        return _SuccessorSelection(candidate=None, budget_exhausted=True)

    visited.add(canonical_state)
    candidate_audit = evaluate_release_predicate(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=config,
    )
    score = _candidate_score(
        current_rows=current_rows,
        candidate_rows=candidate_rows,
        synthetic_row=synthetic_row,
        target_key=target_key,
        reference_rows=reference_rows,
        audit=candidate_audit,
        config=config,
    )
    return _SuccessorSelection(
        candidate=_RankedCandidate(
            rank=(
                candidate_target_severity,
                score,
                _canonical_row(synthetic_row, config.release_columns),
            ),
            rows=candidate_rows,
            audit=candidate_audit,
        )
    )


def _normalize_rows(
    rows: Sequence[Mapping[str, object]], config: KPseudonymityReleaseConfig
) -> tuple[ReleaseRow, ...]:
    if not rows:
        raise KPseudonymityInputError("input release table must not be empty")
    if len(rows) > config.max_input_rows:
        raise KPseudonymityInputError(
            f"input row count exceeds max_input_rows={config.max_input_rows}"
        )
    normalized: list[ReleaseRow] = []
    for row_index, row in enumerate(rows):
        missing = [column for column in config.release_columns if column not in row]
        if missing:
            raise KPseudonymityInputError(
                f"row {row_index} is missing release columns: {', '.join(missing)}"
            )
        normalized_row: ReleaseRow = {}
        for column in config.release_columns:
            value = row[column]
            normalized_row[column] = _normalize_cell(
                value,
                column=column,
                row_index=row_index,
                config=config,
            )
        normalized.append(normalized_row)
    return tuple(normalized)


def _normalize_cell(
    value: object,
    *,
    column: str,
    row_index: int,
    config: KPseudonymityReleaseConfig,
) -> CellValue:
    if value is None or value == "":
        return config.missing_value_token
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise KPseudonymityInputError(
                f"row {row_index} column {column} contains a non-finite number"
            )
        return value
    raise KPseudonymityInputError(
        f"row {row_index} column {column} must contain a scalar value"
    )


def _validate_sensitive_support(
    rows: Sequence[ReleaseRow], config: KPseudonymityReleaseConfig
) -> None:
    combinations_count = 1
    for sensitive in config.sensitive_attributes:
        observed_values = {row[sensitive.name] for row in rows}
        if any(not isinstance(value, str) for value in observed_values):
            raise KPseudonymityInputError(
                f"sensitive attribute {sensitive.name} must contain categorical "
                "string values; continuous values must be pre-binned"
            )
        unexpected_values = observed_values - set(sensitive.allowed_values)
        if unexpected_values:
            raise KPseudonymityInputError(
                f"sensitive attribute {sensitive.name} contains values outside its "
                "pre-specified domain: "
                + ", ".join(sorted(str(value) for value in unexpected_values))
            )
        combinations_count *= len(sensitive.allowed_values)
    if combinations_count > config.max_candidate_combinations:
        raise KPseudonymityInputError(
            "sensitive candidate product exceeds max_candidate_combinations="
            f"{config.max_candidate_combinations}"
        )


def _candidate_synthetic_rows(
    rows: Sequence[ReleaseRow],
    *,
    reference_rows: Sequence[ReleaseRow],
    target_key: ClassKey,
    config: KPseudonymityReleaseConfig,
) -> tuple[ReleaseRow, ...]:
    class_rows = _equivalence_classes(rows, config.quasi_identifiers)[target_key]
    template = class_rows[0]
    supports: list[tuple[CellValue, ...]] = [
        tuple(sensitive.allowed_values) for sensitive in config.sensitive_attributes
    ]

    candidates: list[ReleaseRow] = []
    for sensitive_values in product(*supports):
        candidate = _copy_row(template)
        for sensitive, value in zip(
            config.sensitive_attributes, sensitive_values, strict=True
        ):
            candidate[sensitive.name] = value
        candidates.append(candidate)
    return tuple(candidates)


def _candidate_score(
    *,
    current_rows: Sequence[ReleaseRow],
    candidate_rows: Sequence[ReleaseRow],
    synthetic_row: ReleaseRow,
    target_key: ClassKey,
    reference_rows: Sequence[ReleaseRow],
    audit: ReleasePredicateAudit,
    config: KPseudonymityReleaseConfig,
) -> float:
    target_rows = _equivalence_classes(current_rows, config.quasi_identifiers)[
        target_key
    ]
    template = target_rows[0]
    sensitive_changes = sum(
        _canonical_value(template[item.name])
        != _canonical_value(synthetic_row[item.name])
        for item in config.sensitive_attributes
    )
    reference_distributions = {
        item.name: _categorical_distribution(reference_rows, item.name)
        for item in config.sensitive_attributes
    }
    candidate_target_rows = _equivalence_classes(
        candidate_rows, config.quasi_identifiers
    )[target_key]
    distribution_loss = sum(
        total_variation_distance(
            _categorical_distribution(candidate_target_rows, item.name),
            reference_distributions[item.name],
        )
        for item in config.sensitive_attributes
    )
    weights = config.repair_cost_weights
    synthetic_count = len(candidate_rows) - len(reference_rows)
    return (
        weights.size * synthetic_count
        + weights.sensitive_changes * sensitive_changes
        + weights.distribution * distribution_loss
        + audit.d_util
    )


def _most_severe_violating_class(
    rows: Sequence[ReleaseRow],
    *,
    reference_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> ClassKey | None:
    ranked: list[tuple[float, ClassKey]] = []
    for key, class_rows in _equivalence_classes(rows, config.quasi_identifiers).items():
        severity = _class_violation_severity(
            class_rows,
            reference_rows=reference_rows,
            config=config,
        )
        if severity > 0.0:
            ranked.append((-severity, key))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][1]


def _class_violation_severity(
    class_rows: Sequence[ReleaseRow],
    *,
    reference_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> float:
    severity = max(config.k - len(class_rows), 0) / config.k
    for sensitive in config.sensitive_attributes:
        distribution = _categorical_distribution(class_rows, sensitive.name)
        if sensitive.l_diversity is not None:
            severity += (
                max(sensitive.l_diversity - len(distribution), 0)
                / sensitive.l_diversity
            )
        if sensitive.t_closeness is not None:
            reference_distribution = _categorical_distribution(
                reference_rows, sensitive.name
            )
            tv_distance = total_variation_distance(distribution, reference_distribution)
            severity += max(tv_distance - sensitive.t_closeness, 0.0)
    return severity


def _result(
    *,
    rows: tuple[ReleaseRow, ...],
    config: KPseudonymityReleaseConfig,
    initial_audit: ReleasePredicateAudit,
    final_audit: ReleasePredicateAudit,
    synthetic_indices: tuple[int, ...],
    budget: _SearchBudget,
    status: str,
    reason: str,
) -> KPseudonymityReleaseResult:
    released = status == "released"
    synthetic_count = len(synthetic_indices)
    manifest = KPseudonymityAuditManifest(
        status="released" if released else "no_release",
        reason=reason,
        configuration=config,
        original_row_count=len(rows) - synthetic_count,
        release_row_count=len(rows),
        synthetic_row_count=synthetic_count,
        synthetic_row_proportion=synthetic_count / len(rows),
        synthetic_row_indices=synthetic_indices,
        state_evaluations=budget.evaluations,
        source_table_sha256=_ordered_table_sha256(
            rows[: len(rows) - synthetic_count], config.release_columns
        ),
        release_table_sha256=_ordered_table_sha256(rows, config.release_columns),
        initial_predicate=initial_audit,
        final_predicate=final_audit,
    )
    return KPseudonymityReleaseResult(
        released_rows=tuple(_copy_row(row) for row in rows) if released else None,
        manifest=manifest,
    )


def _copy_row(row: Mapping[str, CellValue]) -> ReleaseRow:
    return dict(row)


def _canonical_table(
    rows: Sequence[ReleaseRow], columns: Sequence[str]
) -> tuple[str, ...]:
    return tuple(sorted(_canonical_row(row, columns) for row in rows))


def _canonical_row(row: ReleaseRow, columns: Sequence[str]) -> str:
    return json.dumps(
        [_canonical_value(row[column]) for column in columns],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _ordered_table_sha256(rows: Sequence[ReleaseRow], columns: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        encoded = _canonical_row(row, columns).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, byteorder="big"))
        digest.update(encoded)
    return digest.hexdigest()


__all__ = [
    "CellValue",
    "KPseudonymityInputError",
    "KPseudonymityReleaseResult",
    "ReleaseRow",
    "build_k_pseudonymous_release",
    "evaluate_release_predicate",
    "jensen_shannon_divergence",
    "total_variation_distance",
    "wasserstein_1_distance",
]
