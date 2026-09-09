from __future__ import annotations

import math

import pytest

from endoreg_db.schemas.k_pseudonymity import KPseudonymityReleaseConfig
from endoreg_db.services import k_pseudonymity as facade
from endoreg_db.services import k_pseudonymity_predicate as predicate
from endoreg_db.services.k_pseudonymity import (
    KPseudonymityInputError,
    ReleaseRow,
    evaluate_release_predicate,
)


def _config(
    *,
    t_closeness: float | None = 0.2,
    tau_max: float = 1.0,
    include_projection_diagnostics: bool = True,
    utility_features: list[dict[str, object]] | None = None,
    release_columns: list[str] | None = None,
    quasi_identifiers: list[str] | None = None,
) -> KPseudonymityReleaseConfig:
    return KPseudonymityReleaseConfig.model_validate(
        {
            "release_columns": release_columns or ["center", "age_band", "diagnosis"],
            "quasi_identifiers": quasi_identifiers or ["center", "age_band"],
            "sensitive_attributes": [
                {
                    "name": "diagnosis",
                    "allowed_values": ["x", "y"],
                    "l_diversity": 2,
                    "t_closeness": t_closeness,
                }
            ],
            "utility_features": utility_features
            or [{"name": "diagnosis", "kind": "categorical", "weight": 1.0}],
            "k": 2,
            "tau_max": tau_max,
            "max_synthetic_rows": 0,
            "include_projection_diagnostics": include_projection_diagnostics,
        }
    )


def test_legacy_service_reexports_predicate_public_contract() -> None:
    assert facade.KPseudonymityInputError is predicate.KPseudonymityInputError
    assert facade.evaluate_release_predicate is predicate.evaluate_release_predicate
    assert facade.jensen_shannon_divergence is predicate.jensen_shannon_divergence
    assert facade.total_variation_distance is predicate.total_variation_distance
    assert facade.wasserstein_1_distance is predicate.wasserstein_1_distance


@pytest.mark.parametrize(
    ("reference_rows", "candidate_rows", "message"),
    [
        ((), (), "reference table must not be empty"),
        (
            ({"center": "a", "age_band": "50-59", "diagnosis": "x"},),
            (),
            "candidate table must not be empty",
        ),
    ],
)
def test_predicate_preserves_empty_table_error_precedence(
    reference_rows: tuple[ReleaseRow, ...],
    candidate_rows: tuple[ReleaseRow, ...],
    message: str,
) -> None:
    with pytest.raises(KPseudonymityInputError, match=message):
        evaluate_release_predicate(
            reference_rows=reference_rows,
            candidate_rows=candidate_rows,
            config=_config(),
        )


def test_predicate_preserves_class_and_violation_ordering() -> None:
    reference_rows: tuple[ReleaseRow, ...] = (
        {"center": "a", "age_band": "50-59", "diagnosis": "x"},
        {"center": "a", "age_band": "50-59", "diagnosis": "y"},
        {"center": "b", "age_band": "60-69", "diagnosis": "x"},
        {"center": "b", "age_band": "60-69", "diagnosis": "y"},
    )
    candidate_rows: tuple[ReleaseRow, ...] = (
        {"center": "b", "age_band": "60-69", "diagnosis": "x"},
        {"center": "a", "age_band": "50-59", "diagnosis": "x"},
        {"center": "b", "age_band": "60-69", "diagnosis": "y"},
    )

    audit = evaluate_release_predicate(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=_config(tau_max=0.0),
    )

    assert audit.feasible is False
    assert audit.violations == (
        "k:str:a|str:50-59:deficit=1",
        "l:diagnosis:str:a|str:50-59:distinct=1",
        "t:diagnosis:str:a|str:50-59:distance=0.5",
        "utility:d_util=0.0207208396239",
    )
    assert [item.quasi_identifier_values for item in audit.equivalence_classes] == [
        {"center": "a", "age_band": "50-59"},
        {"center": "b", "age_band": "60-69"},
    ]
    assert [item.row_count for item in audit.equivalence_classes] == [1, 2]
    assert [item.k_deficit for item in audit.equivalence_classes] == [1, 0]
    first_metric = audit.equivalence_classes[0].sensitive_metrics[0]
    assert first_metric.distinct_value_count == 1
    assert first_metric.l_satisfied is False
    assert first_metric.t_satisfied is False
    assert math.isclose(first_metric.total_variation_distance, 0.5)


@pytest.mark.parametrize(
    ("threshold", "expects_violation"),
    [
        (0.5, False),
        (0.5 - 0.5e-12, False),
        (0.5 - 2.0e-12, True),
    ],
)
def test_predicate_preserves_conservative_t_closeness_tolerance(
    threshold: float,
    expects_violation: bool,
) -> None:
    reference_rows: tuple[ReleaseRow, ...] = (
        {"center": "a", "age_band": "50-59", "diagnosis": "x"},
        {"center": "a", "age_band": "50-59", "diagnosis": "y"},
    )
    candidate_rows: tuple[ReleaseRow, ...] = (
        {"center": "a", "age_band": "50-59", "diagnosis": "x"},
        {"center": "a", "age_band": "50-59", "diagnosis": "x"},
    )

    audit = evaluate_release_predicate(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=_config(t_closeness=threshold),
    )

    has_t_violation = any(item.startswith("t:") for item in audit.violations)
    assert has_t_violation is expects_violation


def test_predicate_preserves_utility_feature_and_projection_order() -> None:
    config = _config(
        t_closeness=None,
        release_columns=["center", "age_band", "diagnosis", "score"],
        utility_features=[
            {"name": "diagnosis", "kind": "categorical", "weight": 0.4},
            {
                "name": "score",
                "kind": "continuous",
                "weight": 0.6,
                "normalization_scale": 10.0,
            },
        ],
    )
    reference_rows: tuple[ReleaseRow, ...] = (
        {"center": "a", "age_band": "50-59", "diagnosis": "x", "score": "1"},
        {"center": "a", "age_band": "50-59", "diagnosis": "y", "score": "3"},
    )
    candidate_rows: tuple[ReleaseRow, ...] = (
        {"center": "a", "age_band": "50-59", "diagnosis": "x", "score": "2"},
        {"center": "a", "age_band": "50-59", "diagnosis": "y", "score": "4"},
    )

    audit = evaluate_release_predicate(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=config,
    )

    assert [item.name for item in audit.utility_features] == ["diagnosis", "score"]
    assert math.isclose(audit.utility_features[0].discrepancy, 0.0)
    assert math.isclose(audit.utility_features[1].discrepancy, 0.1)
    assert [item.quasi_identifiers for item in audit.projection_diagnostics] == [
        ("center",),
        ("age_band",),
        ("center", "age_band"),
    ]

    without_projections = evaluate_release_predicate(
        reference_rows=reference_rows,
        candidate_rows=candidate_rows,
        config=config.model_copy(update={"include_projection_diagnostics": False}),
    )
    assert without_projections.projection_diagnostics == ()


@pytest.mark.parametrize(
    ("value", "message", "has_cause"),
    [
        ("not-numeric", "must be numeric", True),
        ("nan", "must be finite numeric data", False),
        (True, "must be finite numeric data", False),
        (None, "must be finite numeric data", False),
    ],
)
def test_predicate_preserves_numeric_utility_errors_and_chaining(
    value: predicate.CellValue,
    message: str,
    has_cause: bool,
) -> None:
    config = _config(
        t_closeness=None,
        release_columns=["center", "age_band", "diagnosis", "score"],
        utility_features=[
            {
                "name": "score",
                "kind": "continuous",
                "weight": 1.0,
                "normalization_scale": 10.0,
            }
        ],
    )
    reference_rows: tuple[ReleaseRow, ...] = (
        {"center": "a", "age_band": "50-59", "diagnosis": "x", "score": 1},
        {"center": "a", "age_band": "50-59", "diagnosis": "y", "score": 2},
    )
    candidate_rows: tuple[ReleaseRow, ...] = (
        {"center": "a", "age_band": "50-59", "diagnosis": "x", "score": value},
        {"center": "a", "age_band": "50-59", "diagnosis": "y", "score": 2},
    )

    with pytest.raises(KPseudonymityInputError, match=message) as exc_info:
        evaluate_release_predicate(
            reference_rows=reference_rows,
            candidate_rows=candidate_rows,
            config=config,
        )

    assert (exc_info.value.__cause__ is not None) is has_cause


def test_predicate_preserves_canonical_type_distinctions() -> None:
    config = _config(
        t_closeness=None,
        include_projection_diagnostics=False,
        release_columns=["qid", "diagnosis"],
        quasi_identifiers=["qid"],
    )
    rows: tuple[ReleaseRow, ...] = (
        {"qid": True, "diagnosis": "x"},
        {"qid": 1, "diagnosis": "x"},
        {"qid": 1.0, "diagnosis": "x"},
        {"qid": "1", "diagnosis": "x"},
        {"qid": None, "diagnosis": "x"},
    )

    audit = evaluate_release_predicate(
        reference_rows=rows,
        candidate_rows=rows,
        config=config,
    )

    assert len(audit.equivalence_classes) == 5
    assert {
        violation.split(":", maxsplit=2)[1]
        for violation in audit.violations
        if violation.startswith("k:")
    } == {"bool", "int", "float", "str", "none"}
