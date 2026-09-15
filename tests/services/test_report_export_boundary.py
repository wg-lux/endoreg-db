from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from endoreg_db.models.hub.storage_placement import StorageArtifactKind
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.services.hub import storage_artifact_resolution, transfers


def _report_manager(report: object) -> MagicMock:
    manager = MagicMock()
    manager.select_related.return_value.get.return_value = report
    return manager


def test_storage_artifact_contract_has_no_raw_report_kind() -> None:
    assert StorageArtifactKind.PROCESSED_REPORT.value == "processed_report"
    assert all(
        not ("raw" in kind.value and "report" in kind.value)
        for kind in StorageArtifactKind
    )


@pytest.mark.parametrize(
    "state",
    [
        None,
        SimpleNamespace(
            anonymization_validated=False,
            processed_file_sha256="a" * 64,
        ),
    ],
    ids=["missing-state", "not-human-validated"],
)
def test_report_storage_resolution_rejects_unapproved_reports(state: object) -> None:
    report = SimpleNamespace(pk=17, state=state)
    manager = _report_manager(report)

    with (
        patch.object(storage_artifact_resolution.RawPdfFile, "objects", manager),
        patch.object(storage_artifact_resolution, "_resolve") as resolve,
        pytest.raises(
            storage_artifact_resolution.StorageArtifactResolutionError,
            match="processing state|human validated",
        ),
    ):
        storage_artifact_resolution.resolve_current_processed_report_storage(
            report_id=17
        )

    resolve.assert_not_called()


def test_report_storage_resolution_selects_validated_processed_artifact() -> None:
    digest = "b" * 64
    report = SimpleNamespace(
        pk=23,
        state=SimpleNamespace(
            anonymization_validated=True,
            processed_file_sha256=digest,
        ),
    )
    manager = _report_manager(report)
    expected = object()

    with (
        patch.object(storage_artifact_resolution.RawPdfFile, "objects", manager),
        patch.object(
            storage_artifact_resolution,
            "_resolve",
            return_value=expected,
        ) as resolve,
    ):
        result = storage_artifact_resolution.resolve_current_processed_report_storage(
            report_id=23
        )

    assert result is expected
    resolve.assert_called_once_with(
        artifact_key=f"report:23:processed:{digest}",
        artifact_kind=StorageArtifactKind.PROCESSED_REPORT,
        plaintext_sha256=digest,
        media_lease_video_id=None,
    )


def test_encrypted_transfer_boundary_rejects_raw_report_before_staging() -> None:
    transfer_job = cast(
        TransferJob,
        SimpleNamespace(
            transfer_status=TransferJob.TransferStatus.PENDING.value,
            processing_decision=(
                TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA.value
            ),
            transfer_mode=(TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value),
            resource_kind=TransferJob.ResourceKind.REPORT.value,
        ),
    )

    with (
        patch.object(transfers, "prepare_inbound_hub_envelope") as prepare,
        pytest.raises(
            ValueError,
            match="Only anonymized processed media may be attached",
        ),
    ):
        transfers.attach_enveloped_transfer_media(
            transfer_job=transfer_job,
            ciphertext_stream=BytesIO(b"raw report"),
            ciphertext_size=10,
            media_role="raw",
            envelope_json="{}",
        )

    prepare.assert_not_called()
