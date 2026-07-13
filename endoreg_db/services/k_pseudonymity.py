from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations, product
from typing import TypeAlias

from endoreg_db.schemas.k_pseudonymity import (
    EquivalenceClassAudit,
    KPseudonymityAuditManifest,
    KPseudonymityReleaseConfig,
    ProjectionAudit,
    ReleasePredicateAudit,
    SensitiveClassMetric,
    UtilityFeatureAudit,
    UtilityFeatureKind,
)

CellValue: TypeAlias = str | int | float | bool | None
ReleaseRow: TypeAlias = dict[str, CellValue]
ClassKey: TypeAlias = tuple[str, ...]


class KPseudonymityInputError(ValueError):
    """Raised when a release table violates the declared typed boundary."""


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
    current_rows = tuple(_copy_row(row) for row in original_rows)
    synthetic_indices: tuple[int, ...] = ()
    current_audit = initial_audit
    visited = {_canonical_table(current_rows, config.release_columns)}

    if current_audit.feasible:
        return _result(
            rows=current_rows,
            config=config,
            initial_audit=initial_audit,
            final_audit=current_audit,
            synthetic_indices=synthetic_indices,
            budget=budget,
            status="released",
            reason="initial_table_satisfies_release_predicate",
        )

    stop_reason = "no_permitted_unvisited_successor"
    for _ in range(config.max_synthetic_rows):
        target_key = _most_severe_violating_class(
            current_rows,
            reference_rows=reference_rows,
            config=config,
        )
        if target_key is None:
            stop_reason = "utility_threshold_exceeded_without_local_privacy_violation"
            break

        candidates = _candidate_synthetic_rows(
            current_rows,
            reference_rows=reference_rows,
            target_key=target_key,
            config=config,
        )
        best: (
            tuple[
                tuple[float, float, str],
                tuple[ReleaseRow, ...],
                ReleasePredicateAudit,
            ]
            | None
        ) = None
        current_target_severity = _class_violation_severity(
            _equivalence_classes(current_rows, config.quasi_identifiers)[target_key],
            reference_rows=reference_rows,
            config=config,
        )
        for synthetic_row in candidates:
            candidate_rows = (*current_rows, synthetic_row)
            candidate_target_severity = _class_violation_severity(
                _equivalence_classes(candidate_rows, config.quasi_identifiers)[
                    target_key
                ],
                reference_rows=reference_rows,
                config=config,
            )
            if candidate_target_severity >= current_target_severity - 1e-12:
                continue
            canonical_state = _canonical_table(candidate_rows, config.release_columns)
            if canonical_state in visited:
                continue
            if not budget.consume():
                stop_reason = "max_state_evaluations_reached"
                break
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
            tie_breaker = _canonical_row(synthetic_row, config.release_columns)
            ranked = (
                (candidate_target_severity, score, tie_breaker),
                candidate_rows,
                candidate_audit,
            )
            if best is None or ranked[0] < best[0]:
                best = ranked
        if stop_reason == "max_state_evaluations_reached":
            break
        if best is None:
            stop_reason = "no_permitted_unvisited_successor"
            break

        _, current_rows, current_audit = best
        synthetic_indices = (*synthetic_indices, len(current_rows) - 1)
        if current_audit.feasible:
            return _result(
                rows=current_rows,
                config=config,
                initial_audit=initial_audit,
                final_audit=current_audit,
                synthetic_indices=synthetic_indices,
                budget=budget,
                status="released",
                reason="bounded_search_found_feasible_release",
            )
    else:
        stop_reason = "max_synthetic_rows_reached"

    return _result(
        rows=current_rows,
        config=config,
        initial_audit=initial_audit,
        final_audit=current_audit,
        synthetic_indices=synthetic_indices,
        budget=budget,
        status="no_release",
        reason=stop_reason,
    )


def evaluate_release_predicate(
    *,
    reference_rows: Sequence[ReleaseRow],
    candidate_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> ReleasePredicateAudit:
    if not reference_rows:
        raise KPseudonymityInputError("reference table must not be empty")
    if not candidate_rows:
        raise KPseudonymityInputError("candidate table must not be empty")

    reference_distributions = {
        item.name: _categorical_distribution(reference_rows, item.name)
        for item in config.sensitive_attributes
    }
    classes = _equivalence_classes(candidate_rows, config.quasi_identifiers)
    class_audits: list[EquivalenceClassAudit] = []
    violations: list[str] = []
    for class_key in sorted(classes):
        class_rows = classes[class_key]
        sensitive_metrics: list[SensitiveClassMetric] = []
        k_deficit = max(config.k - len(class_rows), 0)
        if k_deficit:
            violations.append(f"k:{_class_label(class_key)}:deficit={k_deficit}")
        for sensitive in config.sensitive_attributes:
            class_distribution = _categorical_distribution(class_rows, sensitive.name)
            tv_distance = total_variation_distance(
                class_distribution,
                reference_distributions[sensitive.name],
            )
            distinct_count = len(class_distribution)
            l_satisfied = (
                sensitive.l_diversity is None or distinct_count >= sensitive.l_diversity
            )
            t_satisfied = sensitive.t_closeness is None or _conservative_leq(
                tv_distance, sensitive.t_closeness
            )
            if not l_satisfied:
                violations.append(
                    f"l:{sensitive.name}:{_class_label(class_key)}:"
                    f"distinct={distinct_count}"
                )
            if not t_satisfied:
                violations.append(
                    f"t:{sensitive.name}:{_class_label(class_key)}:"
                    f"distance={tv_distance:.12g}"
                )
            sensitive_metrics.append(
                SensitiveClassMetric(
                    attribute=sensitive.name,
                    distinct_value_count=distinct_count,
                    l_threshold=sensitive.l_diversity,
                    total_variation_distance=tv_distance,
                    t_threshold=sensitive.t_closeness,
                    l_satisfied=l_satisfied,
                    t_satisfied=t_satisfied,
                )
            )
        class_audits.append(
            EquivalenceClassAudit(
                quasi_identifier_values={
                    name: _display_canonical(value)
                    for name, value in zip(
                        config.quasi_identifiers, class_key, strict=True
                    )
                },
                row_count=len(class_rows),
                k_deficit=k_deficit,
                sensitive_metrics=tuple(sensitive_metrics),
            )
        )

    utility_audits = _utility_audits(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=config,
    )
    d_util = sum(item.weighted_discrepancy for item in utility_audits)
    if not _conservative_leq(d_util, config.tau_max):
        violations.append(f"utility:d_util={d_util:.12g}")

    projections = (
        _projection_audits(candidate_rows, config)
        if config.include_projection_diagnostics
        else ()
    )
    return ReleasePredicateAudit(
        feasible=not violations,
        d_util=d_util,
        tau_max=config.tau_max,
        equivalence_classes=tuple(class_audits),
        projection_diagnostics=projections,
        utility_features=utility_audits,
        violations=tuple(violations),
    )


def total_variation_distance(
    left: Mapping[str, float], right: Mapping[str, float]
) -> float:
    support = set(left) | set(right)
    return 0.5 * sum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in support)


def jensen_shannon_divergence(
    left: Mapping[str, float], right: Mapping[str, float]
) -> float:
    support = set(left) | set(right)
    midpoint = {
        key: 0.5 * (left.get(key, 0.0) + right.get(key, 0.0)) for key in support
    }

    def kl_divergence(source: Mapping[str, float]) -> float:
        result = 0.0
        for key in support:
            probability = source.get(key, 0.0)
            if probability <= 0.0:
                continue
            result += probability * math.log2(probability / midpoint[key])
        return result

    return 0.5 * kl_divergence(left) + 0.5 * kl_divergence(right)


def wasserstein_1_distance(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        raise KPseudonymityInputError(
            "Wasserstein distance requires two non-empty samples"
        )
    left_counts = Counter(left)
    right_counts = Counter(right)
    coordinates = sorted(set(left_counts) | set(right_counts))
    left_cdf = 0.0
    right_cdf = 0.0
    distance = 0.0
    for index, coordinate in enumerate(coordinates[:-1]):
        left_cdf += left_counts[coordinate] / len(left)
        right_cdf += right_counts[coordinate] / len(right)
        width = coordinates[index + 1] - coordinate
        distance += abs(left_cdf - right_cdf) * width
    return distance


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


def _utility_audits(
    *,
    reference_rows: Sequence[ReleaseRow],
    candidate_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> tuple[UtilityFeatureAudit, ...]:
    audits: list[UtilityFeatureAudit] = []
    for feature in config.utility_features:
        if feature.kind is UtilityFeatureKind.CATEGORICAL:
            discrepancy = jensen_shannon_divergence(
                _categorical_distribution(reference_rows, feature.name),
                _categorical_distribution(candidate_rows, feature.name),
            )
        else:
            assert feature.normalization_scale is not None
            discrepancy = (
                wasserstein_1_distance(
                    _numeric_values(reference_rows, feature.name),
                    _numeric_values(candidate_rows, feature.name),
                )
                / feature.normalization_scale
            )
        audits.append(
            UtilityFeatureAudit(
                name=feature.name,
                kind=feature.kind,
                discrepancy=discrepancy,
                weight=feature.weight,
                weighted_discrepancy=feature.weight * discrepancy,
            )
        )
    return tuple(audits)


def _numeric_values(rows: Sequence[ReleaseRow], field: str) -> tuple[float, ...]:
    values: list[float] = []
    for index, row in enumerate(rows):
        value = row[field]
        if isinstance(value, bool) or not isinstance(value, int | float):
            try:
                parsed = float(value) if isinstance(value, str) else None
            except ValueError as exc:
                raise KPseudonymityInputError(
                    f"row {index} utility feature {field} must be numeric"
                ) from exc
            if parsed is None or not math.isfinite(parsed):
                raise KPseudonymityInputError(
                    f"row {index} utility feature {field} must be finite numeric data"
                )
            values.append(parsed)
        else:
            parsed = float(value)
            if not math.isfinite(parsed):
                raise KPseudonymityInputError(
                    f"row {index} utility feature {field} must be finite numeric data"
                )
            values.append(parsed)
    return tuple(values)


def _categorical_distribution(
    rows: Sequence[ReleaseRow], field: str
) -> dict[str, float]:
    counts = Counter(_canonical_value(row[field]) for row in rows)
    total = len(rows)
    return {key: count / total for key, count in counts.items()}


def _equivalence_classes(
    rows: Sequence[ReleaseRow], fields: Sequence[str]
) -> dict[ClassKey, list[ReleaseRow]]:
    result: dict[ClassKey, list[ReleaseRow]] = defaultdict(list)
    for row in rows:
        result[tuple(_canonical_value(row[field]) for field in fields)].append(row)
    return dict(result)


def _projection_audits(
    rows: Sequence[ReleaseRow], config: KPseudonymityReleaseConfig
) -> tuple[ProjectionAudit, ...]:
    audits: list[ProjectionAudit] = []
    for size in range(1, len(config.quasi_identifiers) + 1):
        for subset in combinations(config.quasi_identifiers, size):
            classes = _equivalence_classes(rows, subset)
            class_sizes = [len(class_rows) for class_rows in classes.values()]
            audits.append(
                ProjectionAudit(
                    quasi_identifiers=subset,
                    class_count=len(classes),
                    minimum_class_size=min(class_sizes),
                    underprotected_class_count=sum(
                        class_size < config.k for class_size in class_sizes
                    ),
                )
            )
    return tuple(audits)


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


def _canonical_value(value: CellValue) -> str:
    if value is None:
        return "none:"
    if isinstance(value, bool):
        return f"bool:{str(value).lower()}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        return f"float:{value.hex()}"
    return f"str:{value}"


def _display_canonical(value: str) -> str:
    _, _, display = value.partition(":")
    return display


def _class_label(key: ClassKey) -> str:
    return "|".join(key)


def _conservative_leq(value: float, threshold: float) -> bool:
    tolerance = 1e-12
    return value <= threshold - tolerance or math.isclose(
        value, threshold, rel_tol=0.0, abs_tol=tolerance
    )


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
