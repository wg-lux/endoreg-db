from __future__ import annotations

from fractions import Fraction
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, call

import pytest
from django.utils import timezone
from pydantic import ValidationError
from lx_dtypes.models.contracts.video_provenance import (
    ElectricalTimestampSeries,
    VideoFrameProvenancePayload,
    VideoProvenanceBundle,
    VideoProvenanceRecovery,
)

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models import Center
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.exceptions import MediaOperationDeferred
from endoreg_db.schemas.hub_payloads import TransferProvenancePayload
from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import CanonicalTimelineHistoryEntry
from endoreg_db.services.hub import video_provenance as exchange
from endoreg_db.services.hub.video_provenance import (
    accept_video_provenance,
    electrical_sample_indices,
    record_video_synchronization,
)
from endoreg_db.services.video_storage.frame_provenance import (
    persist_frame_provenance,
    provenance_chain,
    resolve_video_provenance,
)
from tests.services.test_frame_provenance import proof, synchronized_bundle, timeline


def test_sender_receiver_json_round_trip_keeps_exact_frame_lineage() -> None:
    outbound = TransferProvenancePayload(video_frame_provenance=synchronized_bundle())
    received = TransferProvenancePayload.model_validate_json(outbound.model_dump_json())
    video = VideoFile(raw_video_hash="a" * 64, processed_video_hash="b" * 64)
    accept_video_provenance(video, received.video_frame_provenance)
    assert (
        VideoFileMetaPayload.model_validate(video.meta).video_frame_provenance
        == synchronized_bundle()
    )


@pytest.mark.parametrize("field", ["source", "output"])
def test_receiver_rejects_provenance_bound_to_another_artifact(field: str) -> None:
    video = VideoFile(
        raw_video_hash=("f" if field == "source" else "a") * 64,
        processed_video_hash=("f" if field == "output" else "b") * 64,
    )
    with pytest.raises(ValueError):
        accept_video_provenance(video, synchronized_bundle())
    assert not video.meta


@pytest.mark.parametrize(
    "field", ["patient_name", "file_path", "master_key", "shared_secret"]
)
def test_exchange_contract_rejects_private_extras(field: str) -> None:
    payload = synchronized_bundle().model_dump(mode="json")
    payload[field] = "prohibited"
    with pytest.raises(ValidationError):
        VideoProvenanceBundle.model_validate(payload)


@pytest.mark.django_db
def test_multistage_resampling_composes_selected_frame_indices() -> None:
    first = proof()
    second = VideoFrameProvenancePayload(
        source=first.output, output=timeline("c", [0, 50]), source_frame_indices=[1, 2]
    )
    persist_frame_provenance(first)
    persist_frame_provenance(second)
    chain = provenance_chain(source_hash="a" * 64, output_hash="c" * 64)
    bundle = VideoProvenanceBundle(
        transformations=chain.transformations,
        synchronization=synchronized_bundle().synchronization,
    )
    assert electrical_sample_indices(
        bundle,
        samples=ElectricalTimestampSeries(
            session_id="1" * 32,
            clock_id="3" * 32,
            time_base_num=1,
            time_base_den=1000,
            presentation_timestamps=[1000, 1040, 1100, 1160],
        ),
        max_error_seconds=Fraction(1, 1000),
    ) == [2, 3]


@pytest.mark.django_db
def test_recording_anchors_are_persisted_idempotently_and_conflicts_fail(
    mock_video_file: VideoFile,
) -> None:
    video = mock_video_file
    video.raw_video_hash = "a" * 64
    video.meta = {
        "canonical_timeline_history": [
            CanonicalTimelineHistoryEntry(
                artifact_kind="processed",
                before=proof().output,
                after=timeline("c", [0, 50]),
            ).model_dump(mode="json")
        ]
    }
    video.save(update_fields=["raw_video_hash", "meta"])
    sync = synchronized_bundle().synchronization
    assert sync is not None
    record_video_synchronization(video, sync)
    record_video_synchronization(video, sync)
    video.refresh_from_db()
    assert (
        VideoFileMetaPayload.model_validate(video.meta).recording_synchronization
        == sync
    )
    with pytest.raises(ValueError, match="already bound"):
        record_video_synchronization(
            video, sync.model_copy(update={"session_id": "4" * 32})
        )


def test_independent_device_timestamp_units_must_match() -> None:
    samples = ElectricalTimestampSeries(
        session_id="1" * 32,
        clock_id="3" * 32,
        time_base_num=1,
        time_base_den=1000000,
        presentation_timestamps=[1000, 1100, 1160],
    )
    with pytest.raises(ValueError, match="units"):
        electrical_sample_indices(
            synchronized_bundle(), samples=samples, max_error_seconds=Fraction(1)
        )


@pytest.mark.parametrize("ticks", [[1000, 1099, 1100, 1160], [1000, 1001, 1100, 1160]])
def test_synchronization_uncertainty_must_not_cross_sample_midpoint(
    ticks: list[int],
) -> None:
    samples = ElectricalTimestampSeries(
        session_id="1" * 32,
        clock_id="3" * 32,
        time_base_num=1,
        time_base_den=1000,
        presentation_timestamps=ticks,
    )
    with pytest.raises(ValueError, match="uncertainty"):
        electrical_sample_indices(
            synchronized_bundle(), samples=samples, max_error_seconds=Fraction(1)
        )


def test_disconnected_provenance_chain_is_rejected() -> None:
    first = proof()
    second = first.model_copy(update={"output": timeline("c", [0, 40, 80])})
    with pytest.raises(ValidationError, match="disconnected"):
        VideoProvenanceBundle(transformations=[first, second])


@pytest.mark.django_db
@pytest.mark.parametrize("stored_prefix", [0, 1, 2])
@pytest.mark.parametrize("processed_history", [False, True])
def test_resolver_composes_current_generation_without_mutating_metadata(
    stored_prefix: int,
    processed_history: bool,
) -> None:
    first = proof()
    second = VideoFrameProvenancePayload(
        source=first.output, output=timeline("c", [0, 50]), source_frame_indices=[1, 2]
    )
    persist_frame_provenance(first)
    persist_frame_provenance(second)
    expected = VideoProvenanceBundle(transformations=[first, second])
    video = VideoFile(raw_video_hash="a" * 64, processed_video_hash="c" * 64)
    if stored_prefix:
        video.meta = {
            "video_frame_provenance": VideoProvenanceBundle(
                transformations=[first, second][:stored_prefix]
            ).model_dump(mode="json")
        }
    if processed_history:
        video.meta = {
            **(video.meta or {}),
            "canonical_timeline_history": [
                CanonicalTimelineHistoryEntry(
                    artifact_kind="processed", before=second.source, after=second.output
                ).model_dump(mode="json")
            ],
        }
    before = dict(video.meta or {})
    assert resolve_video_provenance(video) == expected
    assert (video.meta or {}) == before


@pytest.mark.django_db
@pytest.mark.parametrize("persisted_bundle", [False, True])
def test_processed_history_cannot_substitute_for_missing_recording_ancestry(
    persisted_bundle: bool,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second = VideoFrameProvenancePayload(
        source=proof().output,
        output=timeline("c", [0, 50]),
        source_frame_indices=[1, 2],
    )
    persist_frame_provenance(second)
    video = VideoFile.objects.create(
        center=Center.objects.create(name="missing-recording-proof"),
        raw_video_hash="a" * 64,
        processed_video_hash="c" * 64,
        processed_file="processed/provenance-test.mp4",
        meta={
            "canonical_timeline_history": [
                CanonicalTimelineHistoryEntry(
                    artifact_kind="processed", before=second.source, after=second.output
                ).model_dump(mode="json")
            ]
        },
    )
    if persisted_bundle:
        video.meta = {
            **(video.meta or {}),
            "video_frame_provenance": VideoProvenanceBundle(
                transformations=[second]
            ).model_dump(mode="json"),
        }
        video.save(update_fields=["meta"])
    validate = Mock()
    monkeypatch.setattr(
        "endoreg_db.services.media.export_downloads.validate_video_download", validate
    )
    caplog.set_level("INFO", logger=exchange.__name__)
    decode = Mock(return_value=second.output)
    monkeypatch.setattr(exchange, "_decode_processed_timeline", decode)
    if persisted_bundle:
        with pytest.raises(ValueError, match="another recording"):
            exchange.export_video_provenance(video)
        validate.assert_not_called()
        decode.assert_not_called()
        assert not MediaOperationLease.objects.filter(video=video).exists()
    else:
        receipt = exchange.export_video_provenance(video)
        assert isinstance(receipt, VideoProvenanceRecovery)
        assert receipt.method == "decoded_processed_timeline_v1"
        assert receipt.processed_timeline == second.output
        assert receipt.original_timeline is None
        assert not receipt.electrical_matching_eligible
        assert validate.call_args_list == [
            call(video, verify_content=False),
            call(video, verify_content=False),
        ]
        [event] = [
            vars(record)["structured_event"]
            for record in caplog.records
            if record.name == exchange.__name__
        ]
        assert event == {
            "event": "hub_transfer.processed_timeline_selected",
            "video_id": video.pk,
            "recording_hash": video.raw_video_hash,
            "processed_hash": video.processed_video_hash,
            "method": receipt.method,
        }
        caplog.clear()
        decode.return_value = second.source
        with pytest.raises(ValueError, match="another processed generation"):
            exchange.export_video_provenance(video)

    assert not any(record.name == exchange.__name__ for record in caplog.records)


@pytest.mark.django_db(transaction=True)
def test_writer_can_replace_generation_during_sender_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django.db import connections, transaction
    from lx_dtypes.models.contracts.video_provenance import VideoFrameTimeline

    video = VideoFile.objects.create(
        center=Center.objects.create(name="concurrent-publication"),
        raw_video_hash="a" * 64,
        processed_video_hash="b" * 64,
        processed_file="processed/provenance-test.mp4",
    )

    def publish() -> None:
        try:
            with transaction.atomic():
                current = VideoFile.objects.select_for_update(nowait=True).get(
                    pk=video.pk
                )
                current.processed_video_hash = "c" * 64
                current.save(update_fields=["processed_video_hash"])
        finally:
            connections.close_all()

    def decode(snapshot: VideoFile) -> VideoFrameTimeline:
        with ThreadPoolExecutor(max_workers=1) as writer:
            writer.submit(publish).result(timeout=10)
        return timeline("b", [0, 10])

    monkeypatch.setattr(exchange, "_decode_processed_timeline", decode)
    monkeypatch.setattr(
        "endoreg_db.services.media.export_downloads.validate_video_download", Mock()
    )
    with pytest.raises(ValueError, match="generation changed"):
        exchange.export_video_provenance(video)
    assert not MediaOperationLease.objects.filter(video=video).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "blocker", ["none", "integrity", "transcode", "expired_writer"]
)
def test_sender_reuses_hash_bound_proof_and_checks_gates_before_export(
    blocker: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level("INFO", logger=exchange.__name__)
    bundle = synchronized_bundle()
    video = VideoFile.objects.create(
        center=Center.objects.create(name="sender-proof-gates"),
        raw_video_hash="a" * 64,
        processed_video_hash="b" * 64,
        processed_file="processed/provenance-test.mp4",
        meta={"video_frame_provenance": bundle.model_dump(mode="json")},
    )
    validate = Mock(
        side_effect=ValueError("integrity mismatch") if blocker == "integrity" else None
    )
    decode = Mock(
        side_effect=AssertionError("Sender must reuse the hash-bound timestamp proof")
    )
    monkeypatch.setattr(
        "endoreg_db.services.media.export_downloads.validate_video_download", validate
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_storage.canonical_timelines.capture_canonical_timeline",
        decode,
    )
    if blocker in {"transcode", "expired_writer"}:
        MediaOperationLease.objects.create(
            video=video,
            lease_type="transcode" if blocker == "transcode" else "artifact_write",
            expires_at=timezone.now()
            + timedelta(seconds=120 if blocker == "transcode" else -120),
        )
        with pytest.raises(MediaOperationDeferred):
            exchange.export_video_provenance(video)
        validate.assert_not_called()
    elif blocker == "integrity":
        with pytest.raises(ValueError, match="integrity mismatch"):
            exchange.export_video_provenance(video)
        validate.assert_called_once_with(video, verify_content=True)
    else:
        assert exchange.export_video_provenance(video) == bundle
        assert validate.call_args_list == [
            call(video, verify_content=True),
            call(video, verify_content=False),
        ]
    assert not any(record.name == exchange.__name__ for record in caplog.records)
    decode.assert_not_called()
    assert MediaOperationLease.objects.filter(
        video=video, lease_type="stream"
    ).exists() == (blocker == "none")


@pytest.mark.django_db
@pytest.mark.parametrize("changed", [False, True])
def test_receiver_still_decodes_the_complete_received_generation(
    changed: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import nullcontext
    from pathlib import Path

    bundle = synchronized_bundle()
    video = VideoFile(raw_video_hash="a" * 64, processed_video_hash="b" * 64)
    decoded = bundle.transformations[-1].output
    if changed:
        decoded = decoded.model_copy(update={"presentation_timestamps": [0, 51, 100]})
    capture = Mock(return_value=decoded)
    monkeypatch.setattr(
        "endoreg_db.utils.storage.ensure_local_file",
        Mock(return_value=nullcontext(Path("processed.mp4"))),
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_storage.probes.probe_video_artifact", Mock()
    )
    monkeypatch.setattr(
        "endoreg_db.services.video_storage.canonical_timelines.capture_canonical_timeline",
        capture,
    )
    if changed:
        with pytest.raises(ValueError, match="decoded processed media"):
            exchange.validate_received_video_timeline(video, bundle)
    else:
        exchange.validate_received_video_timeline(video, bundle)
    capture.assert_called_once()
