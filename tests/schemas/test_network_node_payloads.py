from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from endoreg_db.schemas.network_nodes import (
    NetworkNodePayloadValidationError,
    NetworkNodeResponsePayload,
    NetworkNodeRole,
    dump_network_node_response_payload,
    validate_network_node_create_payload,
)


@pytest.mark.parametrize(
    ("payload", "expected_errors"),
    [
        (
            {"display_name": "Site A", "owning_center_id": "7"},
            {"owning_center_id": "owning_center_id must be an integer or null."},
        ),
        (
            {"display_name": "Site A", "owning_center_key": 7},
            {"owning_center_key": "owning_center_key must be a string or null."},
        ),
        (
            {"display_name": "Site A", "base_url": 7},
            {"base_url": "base_url must be a string."},
        ),
        (
            {"display_name": "Site A", "node_key": 7},
            {"node_key": "node_key must be a string."},
        ),
        (42, {"payload": "Invalid value."}),
    ],
)
def test_network_node_create_maps_strict_boundary_errors(
    payload: object,
    expected_errors: dict[str, str],
) -> None:
    with pytest.raises(NetworkNodePayloadValidationError) as caught:
        validate_network_node_create_payload(payload)

    assert caught.value.errors == expected_errors
    assert isinstance(caught.value.__cause__, ValidationError)


def test_network_node_response_dump_is_canonical_and_excludes_secret() -> None:
    payload = NetworkNodeResponsePayload(
        id=7,
        node_key="site-a",
        display_name="Site A",
        role=NetworkNodeRole.SITE_NODE,
        role_label="Site node",
        base_url="https://site-a.example",
        is_active=True,
        owning_center_id=3,
        owning_center_key="center-a",
        owning_center_name="Center A",
        has_shared_secret=True,
        created_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        updated_at=None,
    )

    dumped = dump_network_node_response_payload(payload)

    assert dumped["role"] == "site_node"
    assert dumped["created_at"] == "2026-08-03T12:00:00Z"
    assert dumped["updated_at"] is None
    assert "shared_secret" not in dumped
