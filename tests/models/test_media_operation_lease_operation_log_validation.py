from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.operation_log import OperationLog


def test_media_operation_lease_metadata_is_canonicalized() -> None:
    lease = MediaOperationLease(
        metadata={"file_type": "  processed  "},
    )

    lease.clean()

    assert lease.metadata == {"file_type": "processed"}


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        {"unexpected": True},
        {"file_type": ""},
        {"source": "unknown"},
    ],
)
def test_media_operation_lease_metadata_rejects_invalid_payloads(
    metadata: object,
) -> None:
    lease = MediaOperationLease(metadata=metadata)

    with pytest.raises(ValidationError) as exc_info:
        lease.clean()

    assert "metadata" in exc_info.value.message_dict


def test_operation_log_meta_is_canonicalized_as_json_object() -> None:
    log = OperationLog(
        action="anonymization.validated",
        meta={
            "timestamp": datetime(2026, 7, 30, 12, 0),
            "path": Path("/tmp/report.pdf"),
            "nested": {"ok": True},
        },
    )

    log.clean()

    assert log.meta == {
        "timestamp": "2026-07-30T12:00:00",
        "path": "/tmp/report.pdf",
        "nested": {"ok": True},
    }


@pytest.mark.parametrize("meta", [[], "not-an-object", {1: "non-string-key"}])
def test_operation_log_meta_rejects_non_json_objects(meta: object) -> None:
    log = OperationLog(action="segment.annotated", meta=meta)

    with pytest.raises(ValidationError) as exc_info:
        log.clean()

    assert "meta" in exc_info.value.message_dict
