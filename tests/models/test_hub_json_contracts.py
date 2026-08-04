from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone

from endoreg_db.models import QuarantineItem, UploadJob


def test_upload_job_processing_provenance_rejects_coerced_values() -> None:
    job = UploadJob(
        processing_provenance={
            "prediction_history_id": "123",
        }
    )

    with pytest.raises(DjangoValidationError) as exc_info:
        job.clean()

    assert "processing_provenance" in exc_info.value.message_dict


def test_upload_job_processing_provenance_strips_strings_and_drops_nulls() -> None:
    job = UploadJob(
        processing_provenance={
            "entrypoint": "  watcher  ",
            "content_hash": "hash-123",
            "source_center_key": None,
        }
    )

    job.clean()

    assert job.processing_provenance == {
        "entrypoint": "watcher",
        "content_hash": "hash-123",
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"file_mtime_ns": "123"},
        {"source_event": 17},
        {"unexpected": True},
    ],
)
def test_quarantine_item_metadata_rejects_non_contract_payloads(
    metadata: object,
) -> None:
    now = timezone.now()
    item = QuarantineItem(
        path="/tmp/endoreg-quarantine/stale.bin",
        relative_path="stale.bin",
        quarantined_at=now,
        last_seen_at=now,
        metadata=metadata,
    )

    with pytest.raises(DjangoValidationError) as exc_info:
        item.clean()

    assert "metadata" in exc_info.value.message_dict


def test_quarantine_item_metadata_canonicalizes_valid_payload() -> None:
    now = timezone.now()
    item = QuarantineItem(
        path="/tmp/endoreg-quarantine/stale.bin",
        relative_path="stale.bin",
        quarantined_at=now,
        last_seen_at=now,
        metadata={
            "source_event": "  quarantine.discovered  ",
            "file_mtime_ns": 123,
            "reason": None,
        },
    )

    item.clean()

    assert item.metadata == {
        "source_event": "quarantine.discovered",
        "file_mtime_ns": 123,
    }
