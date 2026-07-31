from __future__ import annotations

import pytest
from pydantic import ValidationError

from endoreg_db.schemas.anonymization import (
    normalize_categorical_distribution,
    normalize_direct_identifier_tombstone,
)


def test_categorical_distribution_is_sorted_and_preserves_float_values() -> None:
    assert normalize_categorical_distribution({"z": 0.25, "a": 0.75}) == {
        "a": 0.75,
        "z": 0.25,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"a": 1},
        {"a": 0.5, "b": 0.49},
        {"a": -0.1, "b": 1.1},
        {"a": float("nan"), "b": 0.0},
        {"a": 0.5, 1: 0.5},
    ],
)
def test_categorical_distribution_rejects_invalid_payloads(payload: object) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        normalize_categorical_distribution(payload)


def test_clear_tombstone_canonicalizes_iso_timestamp() -> None:
    assert normalize_direct_identifier_tombstone(
        {
            "schema_version": "1.0",
            "policy": "clear_direct_identifiers",
            "cleared_at": "2026-07-31T10:20:30+00:00",
            "cleared_fields_count": 2,
            "cleared_examiners": False,
            "pseudonym_hashes_retained": True,
        }
    )["cleared_at"] == "2026-07-31T10:20:30+00:00"


def test_retained_tombstone_is_strict_and_empty_is_allowed() -> None:
    assert normalize_direct_identifier_tombstone({}) == {}
    assert normalize_direct_identifier_tombstone(
        {
            "schema_version": "1.0",
            "policy": "retain_for_governance",
            "status": "retained_by_policy",
            "direct_values_retained": True,
        }
    )["direct_values_retained"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "1.0", "policy": "unknown"},
        {
            "schema_version": "1.0",
            "policy": "retain_for_governance",
            "status": "retained_by_policy",
            "direct_values_retained": 1,
        },
        {
            "schema_version": "1.0",
            "policy": "clear_direct_identifiers",
            "cleared_at": "not-a-date",
            "cleared_fields_count": 0,
            "cleared_examiners": False,
            "pseudonym_hashes_retained": False,
        },
    ],
)
def test_tombstone_rejects_invalid_payloads(payload: object) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        normalize_direct_identifier_tombstone(payload)
