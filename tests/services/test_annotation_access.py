from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
import pytest
from rest_framework.exceptions import PermissionDenied

from endoreg_db.services.annotation_access import (
    resolve_trusted_annotation_principal,
    validate_interactive_annotation_source,
)


class _Groups:
    def __init__(self, names: set[str] | None = None) -> None:
        self._names = names or set()

    def values_list(self, field_name: str, flat: bool) -> set[str]:
        assert field_name == "name"
        assert flat is True
        return self._names


def _request(
    *,
    username: str = "reviewer",
    authenticated: bool = True,
    staff: bool = False,
) -> Any:
    return cast(
        Any,
        SimpleNamespace(
            user=SimpleNamespace(
                username=username,
                is_authenticated=authenticated,
                is_staff=staff,
                is_superuser=False,
                groups=_Groups(),
            )
        ),
    )


def test_authenticated_principal_is_server_derived() -> None:
    request = _request()

    assert resolve_trusted_annotation_principal(request, None) == "reviewer"
    assert resolve_trusted_annotation_principal(request, "reviewer") == "reviewer"
    with pytest.raises(PermissionDenied, match="Annotator override requires"):
        resolve_trusted_annotation_principal(request, "another-user")


def test_privileged_principal_override_is_explicit() -> None:
    request = _request(staff=True)

    assert (
        resolve_trusted_annotation_principal(request, "external-reviewer")
        == "external-reviewer"
    )


def test_interactive_source_policy_rejects_prediction_provenance() -> None:
    assert validate_interactive_annotation_source("manual_annotation") == (
        "manual_annotation"
    )
    with pytest.raises(PermissionDenied, match="not permitted"):
        validate_interactive_annotation_source("prediction_annotation")
