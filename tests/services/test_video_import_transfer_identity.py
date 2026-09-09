from __future__ import annotations

# pyright: reportPrivateUsage=false

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest
from lx_dtypes.models.contracts.hub_media_envelope import HubMediaEnvelopeReceipt

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import state_management
from endoreg_db.import_files.file_storage.create_video_file import (
    create_or_retrieve_video_file,
)
from endoreg_db.import_files.video_import_service import VideoImportService
from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    NetworkNode,
    TransferJob,
    VideoFile,
)
from endoreg_db.models.state.processing_history import ProcessingHistory
from endoreg_db.services.hls_media import HlsMaterializationResult
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityError,
    has_verified_processed_video_transfer,
)
from endoreg_db.services.hub.transfers import (
    _record_media_upload,
    get_media_envelope_receipt,
)
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    sha256_file,
    safe_unlink_file,
)
from endoreg_db.utils.paths import EndoregPathsModel
from endoreg_db.utils.storage import save_local_file

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class TransferredVideo:
    video: VideoFile
    transfer: TransferJob
    source: Path
    center_name: str
    processor_name: str


@pytest.fixture
def transferred_video(mock_storage: EndoregPathsModel) -> TransferredVideo:
    center = Center.objects.create(name="transferred-video-center")
    processor_name = "received-video-processor"
    processor = EndoscopyProcessor.objects.create(name=processor_name)
    source_node = NetworkNode.objects.create(
        node_key="source-site", display_name="Source", owning_center=center
    )
    target_node = NetworkNode.objects.create(
        node_key="target-hub", display_name="Hub", role=NetworkNode.Role.CENTRAL_HUB
    )
    source = mock_storage.import_video / "renamed-source.mp4"
    atomic_write_file(destination=source, content=(b"original source bytes",))
    processed = mock_storage.sensitive_video / "processed.mp4"
    processed_bytes = b"anonymized generation bytes"
    atomic_write_file(destination=processed, content=(processed_bytes,))
    processed_hash = sha256_file(processed)
    video = VideoFile.objects.create(
        center=center,
        processor=processor,
        video_hash=sha256_file(source),
        processed_video_hash=processed_hash,
        original_file_name="original-source.mp4",
    )
    save_local_file(video.processed_file, processed, name="received.mp4", save=True)
    state = get_or_create_video_state(video)
    state.anonymized = True
    state.anonymization_validated = True
    state.processed_file_sha256 = processed_hash
    state.save()
    ProcessingHistory.mark_success(file_hash=video.video_hash, obj=video)
    transfer = TransferJob.objects.create(
        transfer_key="received-video",
        source_node=source_node,
        target_node=target_node,
        source_center=center,
        resource_kind=TransferJob.ResourceKind.VIDEO,
        resource_hash=video.video_hash,
        target_object_id=video.pk,
        transfer_mode=TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA,
        transfer_status=TransferJob.TransferStatus.APPLIED,
        processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
    )
    receipt = HubMediaEnvelopeReceipt(
        transfer_key=transfer.transfer_key,
        source_node_key=source_node.node_key,
        target_node_key=target_node.node_key,
        source_center_key=center.center_key,
        resource_kind="video",
        resource_hash=video.video_hash,
        processed_media_hash=processed_hash,
        plaintext_sha256=processed_hash,
        plaintext_size=len(processed_bytes),
        recipient_key_id="1" * 64,
        ciphertext_sha256="2" * 64,
        ciphertext_size=len(processed_bytes),
        envelope_fingerprint_sha256="3" * 64,
        receiver_transfer_id=str(transfer.pk),
        processing_decision=transfer.processing_decision,
    )
    stored_name = video.processed_file.name
    assert isinstance(stored_name, str)
    _record_media_upload(
        transfer_job=transfer,
        media_role="processed",
        stored_name=stored_name,
        content_hash=processed_hash,
        envelope_receipt=receipt,
    )
    transfer.save(update_fields=["provenance"])
    video.refresh_from_db()
    assert has_verified_processed_video_transfer(video)
    return TransferredVideo(video, transfer, source, center.name, processor_name)


def test_successful_hub_transfer_is_reused_without_raw_reimport(
    transferred_video: TransferredVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = transferred_video
    video_before = VideoFile.objects.values().get(pk=fixture.video.pk)
    transfer_before = TransferJob.objects.values().get(pk=fixture.transfer.pk)
    master_path = Path(fixture.video.processed_file.path)
    master_before = master_path.read_bytes()
    materialize = Mock(
        return_value=HlsMaterializationResult(
            video_id=fixture.video.pk,
            artifact_kind="processed",
            status="already_ready",
            key_id="test-generation",
            playlist_relative_path="processed/index.m3u8",
            segment_directory_relative_path="processed/segments",
            segment_count=1,
        )
    )
    monkeypatch.setattr(state_management, "materialize_video_hls", materialize)
    anonymizer = Mock()
    service = VideoImportService(anonymizer=anonymizer)
    staging = Mock(
        side_effect=AssertionError("completed transfer must not be restaged")
    )
    monkeypatch.setattr(service, "_ensure_pipeline_storage_budget", staging)

    for _attempt in range(2):
        result = service.import_and_anonymize(
            fixture.source, fixture.center_name, fixture.processor_name
        )
        assert result is not None
        assert result.pk == fixture.video.pk
        ctx = ImportContext(
            file_path=fixture.source,
            center_name=fixture.center_name,
            file_hash=fixture.video.video_hash,
        )
        reused, processed, needs_processing = create_or_retrieve_video_file(ctx)
        assert reused.pk == fixture.video.pk
        assert processed and not needs_processing

    assert [call.kwargs["artifact_kind"] for call in materialize.call_args_list] == [
        "processed",
        "processed",
    ]
    anonymizer.anonymize_video.assert_not_called()
    staging.assert_not_called()
    assert VideoFile.objects.values().get(pk=fixture.video.pk) == video_before
    assert TransferJob.objects.values().get(pk=fixture.transfer.pk) == transfer_before
    assert master_path.read_bytes() == master_before
    assert fixture.source.exists()


@pytest.mark.parametrize(
    "conflict",
    [
        "pending",
        "wrong_target",
        "wrong_center",
        "wrong_source",
        "no_receipt",
        "foreign_receipt",
        "changed_generation",
        "changed_bytes",
        "missing_media",
        "unaccepted_state",
    ],
)
def test_unproven_or_stale_transfer_cannot_authorize_duplicate_success(
    transferred_video: TransferredVideo, conflict: str
) -> None:
    fixture = transferred_video
    transfer = fixture.transfer
    if conflict == "pending":
        transfer.transfer_status = TransferJob.TransferStatus.PENDING
    elif conflict == "wrong_target":
        transfer.target_object_id = fixture.video.pk + 1
    elif conflict == "wrong_center":
        transfer.source_center = Center.objects.create(name="other-center")
    elif conflict == "wrong_source":
        transfer.resource_hash = "b" * 64
    elif conflict == "no_receipt":
        transfer.provenance = {}
    elif conflict == "foreign_receipt":
        receipt = get_media_envelope_receipt(transfer)
        assert receipt is not None
        stored_name = fixture.video.processed_file.name
        assert isinstance(stored_name, str)
        _record_media_upload(
            transfer_job=transfer,
            media_role="processed",
            stored_name=stored_name,
            content_hash=receipt.plaintext_sha256,
            envelope_receipt=receipt.model_copy(
                update={"receiver_transfer_id": "other-transfer"}
            ),
        )
    elif conflict == "changed_generation":
        fixture.video.processed_video_hash = "c" * 64
        fixture.video.save(update_fields=["processed_video_hash"])
    elif conflict == "changed_bytes":
        atomic_write_file(
            destination=Path(fixture.video.processed_file.path), content=(b"corrupt",)
        )
    elif conflict == "missing_media":
        safe_unlink_file(Path(fixture.video.processed_file.path))
    else:
        state = get_or_create_video_state(fixture.video)
        state.anonymization_validated = False
        state.save()
    transfer.save()
    fixture.video.refresh_from_db()

    assert not has_verified_processed_video_transfer(fixture.video)
    ctx = ImportContext(
        file_path=fixture.source,
        center_name=fixture.center_name,
        file_hash=fixture.video.video_hash,
    )
    with pytest.raises(MediaIntegrityError):
        VideoImportService()._get_existing_completed_video(ctx)
    assert VideoFile.objects.filter(pk=fixture.video.pk).exists()
    assert fixture.source.exists()


@pytest.mark.parametrize(
    ("failure", "error_type", "message"),
    [
        ("streaming", RuntimeError, "streaming unavailable"),
        ("forced_retry", MediaIntegrityError, "manual reconciliation"),
        ("foreign_center", ValueError, "different center"),
    ],
)
def test_received_duplicate_failure_preserves_existing_generation(
    transferred_video: TransferredVideo,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    error_type: type[Exception],
    message: str,
) -> None:
    fixture = transferred_video
    before = VideoFile.objects.values().get(pk=fixture.video.pk)
    failed_streaming = Mock(side_effect=RuntimeError("streaming unavailable"))
    monkeypatch.setattr(state_management, "materialize_video_hls", failed_streaming)
    anonymizer = Mock()
    service = VideoImportService(anonymizer=anonymizer)
    with pytest.raises(error_type, match=message):
        service.import_and_anonymize(
            fixture.source,
            "other-center" if failure == "foreign_center" else fixture.center_name,
            fixture.processor_name,
            retry=failure == "forced_retry",
        )
    assert VideoFile.objects.values().get(pk=fixture.video.pk) == before
    assert fixture.source.exists()
    anonymizer.anonymize_video.assert_not_called()
    if failure != "streaming":
        failed_streaming.assert_not_called()
