from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import TypeAlias

from endoreg_db.schemas.k_pseudonymity import (
    EquivalenceClassAudit,
    KPseudonymityReleaseConfig,
    ProjectionAudit,
    ReleasePredicateAudit,
    SensitiveAttributeConfig,
    SensitiveClassMetric,
    UtilityFeatureAudit,
    UtilityFeatureKind,
)

CellValue: TypeAlias = str | int | float | bool | None
ReleaseRow: TypeAlias = dict[str, CellValue]
ClassKey: TypeAlias = tuple[str, ...]


class KPseudonymityInputError(ValueError):
    """Raised when a release table violates the declared typed boundary."""


@dataclass(frozen=True, slots=True)
class _SensitiveMetricResult:
    metric: SensitiveClassMetric
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _EquivalenceClassResult:
    audit: EquivalenceClassAudit
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _EquivalenceClassesResult:
    audits: tuple[EquivalenceClassAudit, ...]
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _UtilityConstraintResult:
    audits: tuple[UtilityFeatureAudit, ...]
    d_util: float
    violations: tuple[str, ...]


def _validate_predicate_tables(
    reference_rows: Sequence[ReleaseRow],
    candidate_rows: Sequence[ReleaseRow],
) -> None:
    if not reference_rows:
        raise KPseudonymityInputError("reference table must not be empty")
    if not candidate_rows:
        raise KPseudonymityInputError("candidate table must not be empty")


def _reference_sensitive_distributions(
    reference_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> dict[str, dict[str, float]]:
    return {
        item.name: categorical_distribution(reference_rows, item.name)
        for item in config.sensitive_attributes
    }


def _evaluate_sensitive_metric(
    *,
    class_key: ClassKey,
    class_rows: Sequence[ReleaseRow],
    sensitive: SensitiveAttributeConfig,
    reference_distribution: Mapping[str, float],
) -> _SensitiveMetricResult:
    class_distribution = categorical_distribution(class_rows, sensitive.name)
    tv_distance = total_variation_distance(
        class_distribution,
        reference_distribution,
    )
    distinct_count = len(class_distribution)
    l_satisfied = (
        sensitive.l_diversity is None or distinct_count >= sensitive.l_diversity
    )
    t_satisfied = sensitive.t_closeness is None or _conservative_leq(
        tv_distance, sensitive.t_closeness
    )
    violations: list[str] = []
    if not l_satisfied:
        violations.append(
            f"l:{sensitive.name}:{_class_label(class_key)}:distinct={distinct_count}"
        )
    if not t_satisfied:
        violations.append(
            f"t:{sensitive.name}:{_class_label(class_key)}:distance={tv_distance:.12g}"
        )
    return _SensitiveMetricResult(
        metric=SensitiveClassMetric(
            attribute=sensitive.name,
            distinct_value_count=distinct_count,
            l_threshold=sensitive.l_diversity,
            total_variation_distance=tv_distance,
            t_threshold=sensitive.t_closeness,
            l_satisfied=l_satisfied,
            t_satisfied=t_satisfied,
        ),
        violations=tuple(violations),
    )


def _evaluate_equivalence_class(
    *,
    class_key: ClassKey,
    class_rows: Sequence[ReleaseRow],
    reference_distributions: Mapping[str, Mapping[str, float]],
    config: KPseudonymityReleaseConfig,
) -> _EquivalenceClassResult:
    k_deficit = max(config.k - len(class_rows), 0)
    violations: list[str] = []
    if k_deficit:
        violations.append(f"k:{_class_label(class_key)}:deficit={k_deficit}")
    sensitive_metrics: list[SensitiveClassMetric] = []
    for sensitive in config.sensitive_attributes:
        result = _evaluate_sensitive_metric(
            class_key=class_key,
            class_rows=class_rows,
            sensitive=sensitive,
            reference_distribution=reference_distributions[sensitive.name],
        )
        sensitive_metrics.append(result.metric)
        violations.extend(result.violations)
    return _EquivalenceClassResult(
        audit=EquivalenceClassAudit(
            quasi_identifier_values={
                name: _display_canonical(value)
                for name, value in zip(config.quasi_identifiers, class_key, strict=True)
            },
            row_count=len(class_rows),
            k_deficit=k_deficit,
            sensitive_metrics=tuple(sensitive_metrics),
        ),
        violations=tuple(violations),
    )


def _evaluate_equivalence_classes(
    *,
    candidate_rows: Sequence[ReleaseRow],
    reference_distributions: Mapping[str, Mapping[str, float]],
    config: KPseudonymityReleaseConfig,
) -> _EquivalenceClassesResult:
    classes = equivalence_classes(candidate_rows, config.quasi_identifiers)
    audits: list[EquivalenceClassAudit] = []
    violations: list[str] = []
    for class_key in sorted(classes):
        result = _evaluate_equivalence_class(
            class_key=class_key,
            class_rows=classes[class_key],
            reference_distributions=reference_distributions,
            config=config,
        )
        audits.append(result.audit)
        violations.extend(result.violations)
    return _EquivalenceClassesResult(
        audits=tuple(audits),
        violations=tuple(violations),
    )


def _evaluate_utility_constraint(
    *,
    reference_rows: Sequence[ReleaseRow],
    candidate_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> _UtilityConstraintResult:
    audits = _utility_audits(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=config,
    )
    d_util = sum(item.weighted_discrepancy for item in audits)
    violations = (
        ()
        if _conservative_leq(d_util, config.tau_max)
        else (f"utility:d_util={d_util:.12g}",)
    )
    return _UtilityConstraintResult(
        audits=audits,
        d_util=d_util,
        violations=violations,
    )


def evaluate_release_predicate(
    *,
    reference_rows: Sequence[ReleaseRow],
    candidate_rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> ReleasePredicateAudit:
    _validate_predicate_tables(reference_rows, candidate_rows)
    reference_distributions = _reference_sensitive_distributions(
        reference_rows,
        config,
    )
    class_result = _evaluate_equivalence_classes(
        candidate_rows=candidate_rows,
        reference_distributions=reference_distributions,
        config=config,
    )
    utility_result = _evaluate_utility_constraint(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=config,
    )
    violations = (*class_result.violations, *utility_result.violations)
    projections = (
        _projection_audits(candidate_rows, config)
        if config.include_projection_diagnostics
        else ()
    )
    return ReleasePredicateAudit(
        feasible=not violations,
        d_util=utility_result.d_util,
        tau_max=config.tau_max,
        equivalence_classes=class_result.audits,
        projection_diagnostics=projections,
        utility_features=utility_result.audits,
        violations=violations,
    )


def total_variation_distance(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    support = set(left) | set(right)
    return 0.5 * sum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in support)


def jensen_shannon_divergence(
    left: Mapping[str, float],
    right: Mapping[str, float],
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


def wasserstein_1_distance(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
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
                categorical_distribution(reference_rows, feature.name),
                categorical_distribution(candidate_rows, feature.name),
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


def _parse_numeric_string(value: str, *, index: int, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise KPseudonymityInputError(
            f"row {index} utility feature {field} must be numeric"
        ) from exc
    if not math.isfinite(parsed):
        raise KPseudonymityInputError(
            f"row {index} utility feature {field} must be finite numeric data"
        )
    return parsed


def _numeric_value(value: CellValue, *, index: int, field: str) -> float:
    if isinstance(value, bool):
        raise KPseudonymityInputError(
            f"row {index} utility feature {field} must be finite numeric data"
        )
    if isinstance(value, str):
        return _parse_numeric_string(value, index=index, field=field)
    if isinstance(value, int | float):
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    raise KPseudonymityInputError(
        f"row {index} utility feature {field} must be finite numeric data"
    )


def _numeric_values(
    rows: Sequence[ReleaseRow],
    field: str,
) -> tuple[float, ...]:
    return tuple(
        _numeric_value(row[field], index=index, field=field)
        for index, row in enumerate(rows)
    )


def categorical_distribution(
    rows: Sequence[ReleaseRow],
    field: str,
) -> dict[str, float]:
    counts = Counter(canonical_value(row[field]) for row in rows)
    total = len(rows)
    return {key: count / total for key, count in counts.items()}


def equivalence_classes(
    rows: Sequence[ReleaseRow],
    fields: Sequence[str],
) -> dict[ClassKey, list[ReleaseRow]]:
    result: dict[ClassKey, list[ReleaseRow]] = defaultdict(list)
    for row in rows:
        result[tuple(canonical_value(row[field]) for field in fields)].append(row)
    return dict(result)


def _projection_audits(
    rows: Sequence[ReleaseRow],
    config: KPseudonymityReleaseConfig,
) -> tuple[ProjectionAudit, ...]:
    audits: list[ProjectionAudit] = []
    for size in range(1, len(config.quasi_identifiers) + 1):
        for subset in combinations(config.quasi_identifiers, size):
            classes = equivalence_classes(rows, subset)
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


def canonical_value(value: CellValue) -> str:
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
        value,
        threshold,
        rel_tol=0.0,
        abs_tol=tolerance,
    )


__all__ = [
    "CellValue",
    "ClassKey",
    "KPseudonymityInputError",
    "ReleaseRow",
    "canonical_value",
    "categorical_distribution",
    "equivalence_classes",
    "evaluate_release_predicate",
    "jensen_shannon_divergence",
    "total_variation_distance",
    "wasserstein_1_distance",
]
