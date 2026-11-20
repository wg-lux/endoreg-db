"""Declared upload center decisions and their lookup wrapper."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from endoreg_db.models import Center
from endoreg_db.services.hub.upload_center_resolution import (
    decide_declared_upload_center,
    resolve_declared_upload_center,
)

KEY_CENTER = Center(pk=1, name="munich", center_key="M-01")
NAME_CENTER = Center(pk=2, name="hamburg", center_key="H-02")


@pytest.mark.parametrize(
    ("center_key", "center_name", "by_key", "by_name", "expected"),
    [
        ("", "", None, None, None),
        ("M-01", "", KEY_CENTER, None, KEY_CENTER),
        ("", "hamburg", None, NAME_CENTER, NAME_CENTER),
        ("M-01", "munich", KEY_CENTER, KEY_CENTER, KEY_CENTER),
        # Existing contract: one known declaration wins over an unknown one.
        ("M-01", "unknown", KEY_CENTER, None, KEY_CENTER),
        ("M-99", "hamburg", None, NAME_CENTER, NAME_CENTER),
    ],
    ids=[
        "undeclared",
        "key",
        "name",
        "matching",
        "known_key_unknown_name",
        "unknown_key_known_name",
    ],
)
def test_decision_resolves_center(
    center_key: str,
    center_name: str,
    by_key: Center | None,
    by_name: Center | None,
    expected: Center | None,
) -> None:
    assert decide_declared_upload_center(
        center_key=center_key,
        center_name=center_name,
        center_by_key=by_key,
        center_by_name=by_name,
    ) == (expected, None)


@pytest.mark.parametrize(
    ("center_key", "center_name", "by_key", "by_name", "error"),
    [
        ("M-99", "", None, None, "Unknown center_key: M-99"),
        ("", "nowhere", None, None, "Unknown center_name: nowhere"),
        ("M-99", "nowhere", None, None, "Unknown center identity"),
        (
            "M-01",
            "hamburg",
            KEY_CENTER,
            NAME_CENTER,
            "center_key and center_name refer to different centers",
        ),
    ],
    ids=["unknown_key", "unknown_name", "unknown_both", "conflict"],
)
def test_decision_reports_errors(
    center_key: str,
    center_name: str,
    by_key: Center | None,
    by_name: Center | None,
    error: str,
) -> None:
    assert decide_declared_upload_center(
        center_key=center_key,
        center_name=center_name,
        center_by_key=by_key,
        center_by_name=by_name,
    ) == (None, error)


@pytest.mark.parametrize(
    ("center_key", "center_name", "by_key", "by_name"),
    [("", "", KEY_CENTER, None), ("", "", None, NAME_CENTER)],
    ids=["key_lookup_without_key", "name_lookup_without_name"],
)
def test_decision_rejects_lookup_without_declaration(
    center_key: str,
    center_name: str,
    by_key: Center | None,
    by_name: Center | None,
) -> None:
    with pytest.raises(ValueError, match="requires its declared identity"):
        decide_declared_upload_center(
            center_key=center_key,
            center_name=center_name,
            center_by_key=by_key,
            center_by_name=by_name,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("center_key", "center_name"),
    [(None, None), ("", ""), ("  ", "\t")],
    ids=["none", "empty", "whitespace"],
)
def test_wrapper_skips_lookups_without_declaration(
    center_key: str | None, center_name: str | None
) -> None:
    with CaptureQueriesContext(connection) as queries:
        result = resolve_declared_upload_center(
            center_key=center_key, center_name=center_name
        )

    assert result == (None, None)
    assert len(queries) == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("center_key", "center_name", "query_count"),
    [(" M-01 ", None, 1), (None, " munich ", 1), (" M-01 ", " munich ", 2)],
    ids=["key", "name", "both"],
)
def test_wrapper_normalizes_and_looks_up_only_declared_values(
    center_key: str | None, center_name: str | None, query_count: int
) -> None:
    center = Center.objects.create(name="munich", center_key="M-01")

    with CaptureQueriesContext(connection) as queries:
        result = resolve_declared_upload_center(
            center_key=center_key, center_name=center_name
        )

    assert result == (center, None)
    assert len(queries) == query_count
