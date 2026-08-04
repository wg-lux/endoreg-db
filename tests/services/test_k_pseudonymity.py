from __future__ import annotations

import math
from copy import deepcopy
from typing import cast

import pytest
from pydantic import ValidationError

from endoreg_db.import_files.pseudonymization.k_pseudonymity import (
    UnsafeLegacyPseudonymizationError,
    k_pseudonymize,
)
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.schemas.k_pseudonymity import KPseudonymityReleaseConfig
from endoreg_db.services.k_pseudonymity import (
    build_k_pseudonymous_release,
    jensen_shannon_divergence,
    wasserstein_1_distance,
)


def _config(**overrides: object) -> KPseudonymityReleaseConfig:
    payload: dict[str, object] = {
        "release_columns": ["center", "age_band", "diagnosis"],
        "quasi_identifiers": ["center", "age_band"],
        "sensitive_attributes": [
            {
                "name": "diagnosis",
                "allowed_values": ["x", "y"],
                "l_diversity": 2,
                "t_closeness": 0.2,
            }
        ],
        "utility_features": [
            {"name": "diagnosis", "kind": "categorical", "weight": 1.0}
        ],
        "k": 2,
        "tau_max": 1.0,
        "max_synthetic_rows": 2,
        "synthetic_rows_count_toward_k": True,
        "recipient_can_observe_synthetic_provenance": False,
    }
    payload.update(overrides)
    return KPseudonymityReleaseConfig.model_validate(payload)


def _repairable_rows() -> list[dict[str, object]]:
    return [
        {"center": "a", "age_band": "50-59", "diagnosis": "x"},
        {"center": "b", "age_band": "60-69", "diagnosis": "x"},
        {"center": "b", "age_band": "60-69", "diagnosis": "y"},
    ]


def test_initially_feasible_table_is_released_without_search() -> None:
    rows = [
        {"center": "a", "age_band": "50-59", "diagnosis": "x"},
        {"center": "a", "age_band": "50-59", "diagnosis": "y"},
        {"center": "b", "age_band": "60-69", "diagnosis": "x"},
        {"center": "b", "age_band": "60-69", "diagnosis": "y"},
    ]

    result = build_k_pseudonymous_release(rows, _config())

    assert result.released_rows is not None
    assert result.manifest.status == "released"
    assert result.manifest.reason == "initial_table_satisfies_release_predicate"
    assert result.manifest.state_evaluations == 1
    assert result.manifest.synthetic_row_count == 0
    assert result.manifest.synthetic_row_indices == ()
    assert result.manifest.source_table_sha256 == result.manifest.release_table_sha256


def test_bounded_release_repairs_k_l_t_without_modifying_real_rows() -> None:
    rows = _repairable_rows()
    original_snapshot = deepcopy(rows)

    result = build_k_pseudonymous_release(rows, _config())

    assert rows == original_snapshot
    assert result.released_rows is not None
    assert len(result.released_rows) == 4
    assert result.manifest.status == "released"
    assert result.manifest.reason == "bounded_search_found_feasible_release"
    assert result.manifest.state_evaluations == 3
    assert result.manifest.synthetic_row_count == 1
    assert result.manifest.synthetic_row_indices == (3,)
    assert result.manifest.real_rows_modified is False
    assert len(result.manifest.source_table_sha256) == 64
    assert len(result.manifest.release_table_sha256) == 64
    assert result.manifest.source_table_sha256 != result.manifest.release_table_sha256
    assert result.manifest.initial_predicate.feasible is False
    assert result.manifest.final_predicate.feasible is True
    assert result.manifest.final_predicate.d_util <= 1.0
    repaired = result.released_rows[3]
    assert repaired["center"] == "a"
    assert repaired["age_band"] == "50-59"
    assert repaired["diagnosis"] == "y"


def test_release_discards_partial_candidate_scan_when_budget_is_exhausted() -> None:
    result = build_k_pseudonymous_release(
        _repairable_rows(),
        _config(max_state_evaluations=2),
    )

    assert result.released_rows is None
    assert result.manifest.status == "no_release"
    assert result.manifest.reason == "max_state_evaluations_reached"
    assert result.manifest.state_evaluations == 2
    assert result.manifest.synthetic_row_count == 0
    assert result.manifest.source_table_sha256 == result.manifest.release_table_sha256
    assert result.manifest.final_predicate == result.manifest.initial_predicate


def test_release_fails_closed_when_utility_threshold_is_exceeded() -> None:
    result = build_k_pseudonymous_release(
        _repairable_rows(),
        _config(tau_max=0.0),
    )

    assert result.released_rows is None
    assert result.manifest.status == "no_release"
    assert (
        result.manifest.reason
        == "utility_threshold_exceeded_without_local_privacy_violation"
    )
    assert result.manifest.state_evaluations == 3
    assert result.manifest.synthetic_row_count == 1
    assert result.manifest.final_predicate.feasible is False
    assert result.manifest.final_predicate.violations
    assert all(
        violation.startswith("utility:")
        for violation in result.manifest.final_predicate.violations
    )


def test_release_stops_at_zero_synthetic_row_limit() -> None:
    result = build_k_pseudonymous_release(
        _repairable_rows(),
        _config(max_synthetic_rows=0),
    )

    assert result.released_rows is None
    assert result.manifest.status == "no_release"
    assert result.manifest.reason == "max_synthetic_rows_reached"
    assert result.manifest.state_evaluations == 1
    assert result.manifest.synthetic_row_count == 0
    assert result.manifest.synthetic_row_indices == ()
    assert result.manifest.source_table_sha256 == result.manifest.release_table_sha256


def test_config_rejects_visible_synthetic_provenance_counting_toward_k() -> None:
    with pytest.raises(ValidationError, match="cannot count toward k"):
        _config(recipient_can_observe_synthetic_provenance=True)


def test_config_rejects_direct_identifiers_in_release_view() -> None:
    with pytest.raises(ValidationError, match="direct identifiers"):
        _config(
            release_columns=["patient_first_name", "center", "diagnosis"],
            quasi_identifiers=["center"],
        )


def test_release_rejects_sensitive_values_outside_predeclared_domain() -> None:
    rows = _repairable_rows()
    rows[0]["diagnosis"] = "undeclared"

    with pytest.raises(ValueError, match="outside its pre-specified domain"):
        build_k_pseudonymous_release(rows, _config())


def test_distance_implementations_use_declared_normalization_semantics() -> None:
    assert math.isclose(
        jensen_shannon_divergence({"x": 1.0}, {"y": 1.0}),
        1.0,
    )
    assert math.isclose(
        wasserstein_1_distance((0.0, 2.0), (1.0, 3.0)),
        1.0,
    )


def test_legacy_mutating_pseudonymizer_is_disabled() -> None:
    with pytest.raises(UnsafeLegacyPseudonymizationError, match="disabled"):
        k_pseudonymize(cast(SensitiveMeta, object()))
