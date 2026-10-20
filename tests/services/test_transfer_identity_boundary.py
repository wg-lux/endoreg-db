from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import ValidationError

from endoreg_db.models import NetworkNode
from endoreg_db.serializers.hub.transfer_job import TransferJobCreateSerializer
from endoreg_db.services.hub.payloads import reject_transferred_identity_hashes
from endoreg_db.services.hub.transfers import create_or_reuse_transfer_job

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "field", ["patient_hash", "examination_hash", "identity_fingerprint"]
)
@pytest.mark.parametrize("value", ["a" * 64, "", None])
def test_identity_fields_rejected_without_disclosure(field: str, value: object) -> None:
    payload: dict[str, object] = {"provenance": {"nested": [{field: value}]}}
    with pytest.raises(ValueError, match="must remain on the source") as error:
        reject_transferred_identity_hashes(payload)
    assert "a" * 64 not in str(error.value)
    with pytest.raises(ValidationError, match="must remain on the source"):
        TransferJobCreateSerializer().validate(payload)


def test_file_integrity_hashes_remain_allowed() -> None:
    reject_transferred_identity_hashes(
        {
            "resource_hash": "a" * 64,
            "resource_rows": {"video_file": {"processed_video_hash": "b" * 64}},
            "processing_snapshot": {"processed_file_sha256": "c" * 64},
        }
    )


def test_direct_registration_rejects_identity_before_database_access() -> None:
    node = cast(NetworkNode, MagicMock(spec=NetworkNode))
    with pytest.raises(ValueError, match="must remain on the source"):
        create_or_reuse_transfer_job(
            transfer_key="blocked",
            source_node=node,
            target_node=node,
            source_center=None,
            resource_kind="report",
            resource_hash="a" * 64,
            transfer_mode="metadata_only",
            processing_policy="preserve_processing_state",
            processing_intent="state_preservation",
            cleanup_policy="retain_all",
            payload_schema_version="3.0",
            resource_rows={"sensitive_meta": {"patient_hash": "b" * 64}},
            processing_snapshot={},
            provenance={},
        )


@pytest.mark.parametrize("version", ["1.0", "2.0", "3.0", "4.0"])
def test_transfer_rejects_pre_uuid_protocol_versions(version: str) -> None:
    with pytest.raises(ValidationError, match="5.0"):
        TransferJobCreateSerializer().validate_payload_schema_version(version)
