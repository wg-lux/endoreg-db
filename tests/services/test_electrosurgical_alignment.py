"""Electrosurgical measurement import, PTS alignment and read API invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from lx_dtypes.models.contracts.electrical_video_alignment import (
    ElectricalFrameAggregationProfile,
)
from lx_dtypes.models.contracts.video_provenance import (
    RecordingSynchronization,
    SynchronizationAnchor,
    VideoFrameProvenancePayload,
    VideoFrameTimeline,
    VideoProvenanceRecovery,
)
from rest_framework.test import APIClient

from endoreg_db.models import PortalUserInfo, VideoFile
from endoreg_db.models.media.video.electrosurgical_measurement import (
    VideoElectrosurgicalMeasurement,
)
from endoreg_db.services.electrical.measurement_import import (
    ElectrosurgicalImportError,
    import_electrosurgical_measurement,
)
from endoreg_db.services.electrical.video_alignment import (
    ElectricalAlignmentRefusal,
    ElectricalAlignmentUnavailable,
    align_measurement_frames,
)
from endoreg_db.services.hub.video_provenance import accept_video_provenance_recovery
from endoreg_db.services.video_storage.frame_provenance import (
    persist_frame_provenance,
)
from endoreg_db.utils.encryption.encrypted import MAGIC

pytestmark = pytest.mark.django_db

RAW = "a" * 64
PROCESSED = "b" * 64
NEXT_GENERATION = "c" * 64
SESSION = "1" * 32
VIDEO_CLOCK = "2" * 32
ELECTRICAL_CLOCK = "3" * 32
SOURCE = "e" * 64
# Electrical samples every 10 ms; their values equal their sample index.
TICKS = list(range(1000, 1200, 10))


def _timeline(digest: str, ticks: list[int]) -> VideoFrameTimeline:
    return VideoFrameTimeline(
        content_hash=digest,
        time_base_num=1,
        time_base_den=1000,
        presentation_timestamps=ticks,
    )


# Variable frame rate: recording frame intervals of 20, 30, 30 and 80 ms. The
# processed generation drops recording frame 1, so frame 0's window spans 50 ms.
RECORDING = _timeline(RAW, [100, 120, 150, 180, 260])
GENERATION = _timeline(PROCESSED, [0, 40, 80, 120])
SYNC = RecordingSynchronization(
    session_id=SESSION,
    video_clock_id=VIDEO_CLOCK,
    electrical_clock_id=ELECTRICAL_CLOCK,
    source_content_hash=RAW,
    electrical_time_base_num=1,
    electrical_time_base_den=1000,
    method="shared_hardware_event",
    anchors=[
        SynchronizationAnchor(video_timestamp=100, electrical_timestamp=1000),
        SynchronizationAnchor(video_timestamp=300, electrical_timestamp=1200),
    ],
    uncertainty_num=1,
    uncertainty_den=1000,
)
PROFILE = ElectricalFrameAggregationProfile(
    profile_id="test_frame_summary",
    profile_version=1,
    max_sample_gap_num=2,
    max_sample_gap_den=100,
    max_alignment_uncertainty_num=1,
    max_alignment_uncertainty_den=1000,
)


def series_payload(
    *,
    ticks: list[int] = TICKS,
    source: str = SOURCE,
    session: str = SESSION,
    parser_version: str = "1",
    channels: list[dict[str, object]] | None = None,
) -> bytes:
    return json.dumps(
        {
            "schema_version": "1",
            "source_sha256": source,
            "parser_id": "test_parser",
            "parser_version": parser_version,
            "timestamps": {
                "session_id": session,
                "clock_id": ELECTRICAL_CLOCK,
                "time_base_num": 1,
                "time_base_den": 1000,
                "presentation_timestamps": ticks,
            },
            "channels": channels
            if channels is not None
            else [
                {
                    "channel_id": "power",
                    "quantity": "power",
                    "unit": "W",
                    "values": [float(index) for index in range(len(ticks))],
                },
                {
                    "channel_id": "activation",
                    "quantity": "activation",
                    "unit": "1",
                    "values": [1.0] * len(ticks),
                },
            ],
        }
    ).encode()


@pytest.fixture
def video(mock_video_file: VideoFile) -> VideoFile:
    persist_frame_provenance(
        VideoFrameProvenancePayload(
            source=RECORDING, output=GENERATION, source_frame_indices=[0, 2, 3, 4]
        )
    )
    VideoFile.objects.filter(pk=mock_video_file.pk).update(
        raw_video_hash=RAW, processed_video_hash=PROCESSED, meta={}
    )
    mock_video_file.refresh_from_db()
    return mock_video_file


def _import(
    video: VideoFile, payload: bytes | None = None
) -> VideoElectrosurgicalMeasurement:
    try:
        return import_electrosurgical_measurement(
            video.pk,
            series_payload=payload or series_payload(),
            profile=PROFILE,
            synchronization=SYNC,
        ).measurement
    finally:
        # The service records anchors on its own locked row.
        video.refresh_from_db()


def test_valid_import_stores_ciphertext_and_typed_metadata(video: VideoFile) -> None:
    result = import_electrosurgical_measurement(
        video.pk, series_payload=series_payload(), profile=PROFILE, synchronization=SYNC
    )
    measurement = result.measurement
    assert result.created
    assert measurement.sample_count == len(TICKS)
    assert (measurement.first_sample_timestamp, measurement.last_sample_timestamp) == (
        1000,
        1190,
    )
    assert [channel["unit"] for channel in measurement.channels] == ["W", "1"]
    assert measurement.recording_content_hash == RAW
    stored = Path(measurement.source_file.path).read_bytes()
    assert stored.startswith(MAGIC)
    assert b"test_parser" not in stored


def test_reimport_is_idempotent_and_conflicts_fail(video: VideoFile) -> None:
    first = _import(video)
    again = import_electrosurgical_measurement(
        video.pk, series_payload=series_payload(), profile=PROFILE
    )
    assert not again.created and again.measurement.pk == first.pk
    with pytest.raises(ElectrosurgicalImportError, match="conflicting re-import"):
        _import(video, series_payload(parser_version="2"))
    with pytest.raises(ElectrosurgicalImportError, match="conflicting re-import"):
        import_electrosurgical_measurement(
            video.pk,
            series_payload=series_payload(),
            profile=PROFILE.model_copy(update={"profile_version": 2}),
        )
    assert VideoElectrosurgicalMeasurement.objects.count() == 1


@pytest.mark.parametrize(
    "payload",
    [
        series_payload(ticks=[1000, 1010, 1010]),
        series_payload(ticks=[1000, 990]),
        series_payload(
            channels=[
                {
                    "channel_id": "power",
                    "quantity": "power",
                    "unit": "V",
                    "values": [1.0] * 20,
                }
            ]
        ),
        series_payload(
            channels=[
                {
                    "channel_id": "on",
                    "quantity": "activation",
                    "unit": "1",
                    "values": [0.5] * 20,
                }
            ]
        ),
        series_payload(
            channels=[
                {
                    "channel_id": "power",
                    "quantity": "power",
                    "unit": "W",
                    "values": [1.0],
                }
            ]
        ),
        series_payload(source="not-a-hash"),
        b"{not json",
    ],
    ids=[
        "duplicate",
        "non_monotonic",
        "unit",
        "activation",
        "length",
        "source",
        "syntax",
    ],
)
def test_invalid_series_is_rejected_without_persistence(
    video: VideoFile, payload: bytes
) -> None:
    with pytest.raises(ElectrosurgicalImportError, match="contract validation"):
        _import(video, payload)
    assert not VideoElectrosurgicalMeasurement.objects.exists()


def test_import_requires_matching_synchronization(video: VideoFile) -> None:
    with pytest.raises(ElectrosurgicalImportError, match="synchronization anchors"):
        import_electrosurgical_measurement(
            video.pk, series_payload=series_payload(), profile=PROFILE
        )
    with pytest.raises(ElectrosurgicalImportError, match="session or clock"):
        _import(video, series_payload(session="4" * 32))
    assert not VideoElectrosurgicalMeasurement.objects.exists()


def test_frame_windows_follow_pts_not_nominal_fps(video: VideoFile) -> None:
    measurement = _import(video)
    page = align_measurement_frames(
        video,
        measurement,
        expected_generation_content_hash=PROCESSED,
        first_frame=0,
        frame_count=10,
    )
    frames = page.alignment.frames
    assert [frame.status for frame in frames] == [
        "complete",
        "complete",
        "complete",
        "unbounded_interval",
    ]
    # Windows: [100,150) -> samples 0..4, [150,180) -> 5..7, [180,260) -> 8..15.
    assert [(f.sample_start_index, f.sample_end_index) for f in frames[:3]] == [
        (0, 5),
        (5, 8),
        (8, 16),
    ]
    power = [next(c for c in f.channels if c.channel_id == "power") for f in frames[:3]]
    assert [(c.mean, c.minimum, c.maximum) for c in power] == [
        (2.0, 0.0, 4.0),
        (6.0, 5.0, 7.0),
        (11.5, 8.0, 15.0),
    ]
    assert page.presentation_time_seconds == [0.0, 0.04, 0.08, 0.12]
    assert frames[3].channels == [] and frames[3].recording_end_timestamp is None
    assert page.alignment.status == "incomplete"


def test_sample_gaps_are_reported_not_filled(video: VideoFile) -> None:
    gapped = [tick for tick in TICKS if not 1090 <= tick <= 1130]
    measurement = _import(video, series_payload(ticks=gapped))
    frames = align_measurement_frames(
        video,
        measurement,
        expected_generation_content_hash=PROCESSED,
        first_frame=0,
        frame_count=3,
    ).alignment.frames
    assert [frame.status for frame in frames] == ["complete", "complete", "sample_gap"]
    assert frames[2].channels == []


def test_ranges_are_bounded_and_clipped(video: VideoFile) -> None:
    measurement = _import(video)
    page = align_measurement_frames(
        video,
        measurement,
        expected_generation_content_hash=PROCESSED,
        first_frame=1,
        frame_count=2,
        channel_ids=("power",),
    )
    assert [f.output_frame_index for f in page.alignment.frames] == [1, 2]
    assert page.next_first_frame == 3
    assert {c.channel_id for f in page.alignment.frames for c in f.channels} == {
        "power"
    }
    clipped = align_measurement_frames(
        video,
        measurement,
        expected_generation_content_hash=PROCESSED,
        first_frame=2,
        frame_count=5000,
    )
    assert (clipped.frame_count, clipped.next_first_frame) == (2, None)
    for first, count, code in [
        (4, 1, ElectricalAlignmentRefusal.INVALID_RANGE),
        (0, 5001, ElectricalAlignmentRefusal.INVALID_RANGE),
        (0, 0, ElectricalAlignmentRefusal.INVALID_RANGE),
    ]:
        with pytest.raises(ElectricalAlignmentUnavailable) as caught:
            align_measurement_frames(
                video,
                measurement,
                expected_generation_content_hash=PROCESSED,
                first_frame=first,
                frame_count=count,
            )
        assert caught.value.code == code
    with pytest.raises(ElectricalAlignmentUnavailable) as unknown:
        align_measurement_frames(
            video,
            measurement,
            expected_generation_content_hash=PROCESSED,
            first_frame=0,
            frame_count=1,
            channel_ids=("voltage",),
        )
    assert unknown.value.code == ElectricalAlignmentRefusal.UNKNOWN_CHANNEL


def test_new_generation_invalidates_previous_identity(video: VideoFile) -> None:
    measurement = _import(video)
    persist_frame_provenance(
        VideoFrameProvenancePayload(
            source=GENERATION,
            output=_timeline(NEXT_GENERATION, [0, 80]),
            source_frame_indices=[0, 2],
        )
    )
    VideoFile.objects.filter(pk=video.pk).update(processed_video_hash=NEXT_GENERATION)
    video.refresh_from_db()
    with pytest.raises(ElectricalAlignmentUnavailable) as stale:
        align_measurement_frames(
            video,
            measurement,
            expected_generation_content_hash=PROCESSED,
            first_frame=0,
            frame_count=2,
        )
    assert stale.value.code == ElectricalAlignmentRefusal.STALE_GENERATION
    page = align_measurement_frames(
        video,
        measurement,
        expected_generation_content_hash=NEXT_GENERATION,
        first_frame=0,
        frame_count=2,
    )
    assert page.alignment.generation_content_hash == NEXT_GENERATION
    assert page.alignment.measurement_source_sha256 == SOURCE
    assert [f.recording_frame_index for f in page.alignment.frames] == [0, 3]
    # Composed ancestry: the first frame now spans recording frames 0..2.
    assert page.alignment.frames[0].recording_end_timestamp == 180


def test_unresolved_provenance_refuses_alignment(video: VideoFile) -> None:
    measurement = _import(video)
    accept_video_provenance_recovery(
        video,
        VideoProvenanceRecovery(
            recording_hash=RAW, processed_timeline=GENERATION
        ).model_dump(mode="json"),
    )
    video.save(update_fields=["meta"])
    with pytest.raises(ElectricalAlignmentUnavailable) as refused:
        align_measurement_frames(
            video,
            measurement,
            expected_generation_content_hash=PROCESSED,
            first_frame=0,
            frame_count=1,
        )
    assert refused.value.code == ElectricalAlignmentRefusal.PROVENANCE_UNRESOLVED


def test_tampered_document_identity_fails_closed(video: VideoFile) -> None:
    measurement = _import(video)
    VideoElectrosurgicalMeasurement.objects.filter(pk=measurement.pk).update(
        payload_sha256="0" * 64
    )
    measurement.refresh_from_db()
    with pytest.raises(ElectricalAlignmentUnavailable) as refused:
        align_measurement_frames(
            video,
            measurement,
            expected_generation_content_hash=PROCESSED,
            first_frame=0,
            frame_count=1,
        )
    assert refused.value.code == ElectricalAlignmentRefusal.SOURCE_INTEGRITY_FAILED


def test_alignment_never_consults_nominal_fps(video: VideoFile) -> None:
    measurement = _import(video)
    VideoFile.objects.filter(pk=video.pk).update(fps=1000.0)
    video.refresh_from_db()
    page = align_measurement_frames(
        video,
        measurement,
        expected_generation_content_hash=PROCESSED,
        first_frame=0,
        frame_count=4,
    )
    assert page.presentation_time_seconds == [0.0, 0.04, 0.08, 0.12]


@pytest.fixture
def client_user(video: VideoFile) -> tuple[APIClient, User]:
    user = User.objects.create_user(username="electrical-viewer")
    PortalUserInfo.objects.create(user=user).centers.add(video.center)
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def test_api_lists_and_aligns_with_documented_errors(
    video: VideoFile, client_user: tuple[APIClient, User]
) -> None:
    client, user = client_user
    base = f"/api/media/videos/{video.pk}/electrical-measurements/"
    empty = client.get(base)
    assert empty.status_code == 200
    assert empty.json()["measurements"] == []
    measurement = _import(video)
    summary = client.get(base).json()
    assert summary["electrical_matching_eligible"] is True
    assert summary["generation_content_hash"] == PROCESSED
    assert summary["total_frame_count"] == 4
    assert summary["measurements"][0]["channels"][0] == {
        "channel_id": "power",
        "quantity": "power",
        "unit": "W",
    }
    frames_url = f"{base}{measurement.pk}/frames/"
    ok = client.get(
        frames_url,
        {
            "generation_content_hash": PROCESSED,
            "first_frame": 0,
            "frame_count": 2,
            "channel_id": "power",
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["range"] == {
        "first_frame": 0,
        "frame_count": 2,
        "total_frame_count": 4,
        "next_first_frame": 2,
    }
    assert body["alignment"]["algorithm"] == "electrical_frame_window_v1"
    assert body["alignment"]["generation_content_hash"] == PROCESSED
    assert body["frames"][1]["presentation_time_seconds"] == 0.04
    assert "values" not in json.dumps(body)

    def code(response: object) -> tuple[int, str]:
        return response.status_code, response.json()["code"]  # type: ignore[attr-defined]

    assert code(client.get(frames_url)) == (400, "invalid_request")
    assert code(
        client.get(
            frames_url, {"generation_content_hash": PROCESSED, "frame_count": 5001}
        )
    ) == (400, "invalid_request")
    assert code(
        client.get(frames_url, {"generation_content_hash": PROCESSED, "bogus": 1})
    ) == (400, "invalid_request")
    assert code(
        client.get(frames_url, {"generation_content_hash": NEXT_GENERATION})
    ) == (409, "stale_generation")
    assert code(
        client.get(
            frames_url, {"generation_content_hash": PROCESSED, "channel_id": "voltage"}
        )
    ) == (400, "unknown_channel")
    assert code(
        client.get(
            f"{base}{measurement.pk + 999}/frames/",
            {"generation_content_hash": PROCESSED},
        )
    ) == (404, "measurement_not_found")

    PortalUserInfo.objects.get(user=user).centers.clear()
    assert client.get(base).status_code == 403
    assert APIClient().get(base).status_code in (401, 403)
