from __future__ import annotations

import pytest
from django.db import transaction
from pydantic import ValidationError

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimestampTransformation,
    CanonicalTimelineHistoryEntry,
    SegmentTimelineReference,
    VideoArtifactProbe,
    VideoTimelineContract,
    NominalFrameRate,
)
from endoreg_db.services.video_files.types import VideoArtifactKind
from endoreg_db.services.video_storage.canonical_timelines import (
    append_canonical_timeline_history,
)
from endoreg_db.services.video_storage.contracts import (
    configured_video_storage_profile,
    VideoStorageNormalizationError,
)
from endoreg_db.services.video_storage.processed_timelines import (
    publish_processed_coordinates,
    nearest_boundary,
)
from endoreg_db.services.video_storage.validation import validate_normalized_output
from endoreg_db.models import (
    VideoFile,
    Center,
    Frame,
    Label,
    LabelVideoSegment,
    ImageClassificationAnnotation,
    VideoSegmentationAnnotation,
    VideoSegmentationLabel,
)
from endoreg_db.services.media.export_downloads import timestamp_csv


def snapshot(ticks: list[int], content: str) -> CanonicalFrameTimeline:
    return CanonicalFrameTimeline(
        content_hash=content * 64,
        time_base_num=1,
        time_base_den=1000,
        presentation_timestamps=ticks,
    )


def probe(count: int = 4, nominal: int = 25) -> VideoArtifactProbe:
    return VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=64,
        height=64,
        bit_rate_bps=800000,
        size_bytes=1000,
        timeline=VideoTimelineContract(
            fps_num=25,
            fps_den=1,
            duration_seconds=0.16,
            frame_count=count,
            time_base_num=1,
            time_base_den=1000,
            nominal_frame_rate=NominalFrameRate(numerator=nominal, denominator=1),
        ),
    )


def reference(
    segment_id: int = 1, start: int = 1, end: int = 3
) -> SegmentTimelineReference:
    return SegmentTimelineReference(
        segment_id=segment_id,
        start_frame=start,
        end_frame=end,
        start_timestamp_seconds=1.4 + start * 0.04,
        end_timestamp_seconds=1.4 + end * 0.04,
    )


@pytest.mark.parametrize("nominal", [25, 50, 1000])
def test_processed_native_proof_overrides_container_rate_metadata(nominal: int) -> None:
    evidence = validate_normalized_output(
        source=probe(),
        output=probe(nominal=nominal),
        profile=configured_video_storage_profile(),
        artifact_kind=VideoArtifactKind.PROCESSED,
        segments=[reference()],
        canonical_timestamps=CanonicalTimestampTransformation(
            before=snapshot([1400, 1440, 1480, 1520], "a"),
            after=snapshot([0, 20, 45, 120], "b"),
        ),
    )
    mapping = evidence.segment_mappings[0]
    assert mapping.before == reference()
    assert mapping.after.start_frame == 2 and mapping.after.end_frame == 3
    assert mapping.after.start_timestamp_seconds == 0.045
    assert mapping.after.end_timestamp_seconds == 0.12


@pytest.mark.parametrize(
    "target,expected", [(0.04, 1), (0.055, 1), (0.09, 2), (0.16, 4)]
)
def test_nearest_processed_pts_ties_choose_earlier(
    target: float, expected: int
) -> None:
    assert nearest_boundary([0.0, 0.04, 0.07, 0.12, 0.16], target) == expected


@pytest.mark.parametrize(
    "ticks,segment",
    [
        ([0, 40, 80], reference()),
        ([0, 1, 2, 120], reference()),
        ([0, 20, 45, 120], reference(start=1, end=2)),
    ],
)
def test_dropped_frames_large_drift_and_collapsed_segments_fail(
    ticks: list[int], segment: SegmentTimelineReference
) -> None:
    with pytest.raises(VideoStorageNormalizationError):
        validate_normalized_output(
            source=probe(),
            output=probe(count=len(ticks)),
            profile=configured_video_storage_profile(),
            artifact_kind=VideoArtifactKind.PROCESSED,
            segments=[segment],
            canonical_timestamps=CanonicalTimestampTransformation(
                before=snapshot([1400, 1440, 1480, 1520], "a"),
                after=snapshot(ticks, "b"),
            ),
        )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure", [None, "stale_segment", "time_segment", "outside"])
@pytest.mark.parametrize("origin", [1.4, 0.5])
def test_publication_maps_processed_borders_and_retains_raw_pts_and_annotations(
    failure: str | None,
    origin: float,
) -> None:
    raw = snapshot([1400, 1440, 1480, 1520], "a")
    video = VideoFile.objects.create(
        center=Center.objects.create(name="processed-pts"),
        raw_video_hash=raw.content_hash,
        processed_video_hash="b" * 64,
        processed_file="processed.mp4",
        fps=25,
        duration=0.16,
        frame_count=4,
        meta={
            "canonical_timeline_history": [
                CanonicalTimelineHistoryEntry(
                    artifact_kind="raw", before=raw, after=raw
                ).model_dump(mode="json")
            ]
        },
    )
    frames = [
        Frame.objects.create(
            video=video,
            frame_number=index,
            timestamp=origin + index * 0.04,
            presentation_timestamp=tick,
        )
        for index, tick in enumerate(raw.presentation_timestamps)
    ]
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        start_frame_number=1,
        end_frame_number=3,
        label=Label.objects.create(name="outside") if failure == "outside" else None,
    )
    time_segment = VideoSegmentationAnnotation.objects.create(
        video_file=video,
        label=VideoSegmentationLabel.objects.create(name="time-label"),
        start_time=origin + 0.04,
        stop_time=origin + (0.08 if failure == "time_segment" else 0.12),
    )
    annotation = ImageClassificationAnnotation.objects.create(
        frame=frames[1], label=Label.objects.create(name="kept"), value=True
    )
    annotation_before = (
        ImageClassificationAnnotation.objects.filter(pk=annotation.pk).values().get()
    )
    original_export = list(timestamp_csv(video, original=True))
    evidence = validate_normalized_output(
        source=probe(),
        output=probe(nominal=50),
        profile=configured_video_storage_profile(),
        artifact_kind=VideoArtifactKind.PROCESSED,
        segments=[
            reference(segment.pk).model_copy(
                update={
                    "start_timestamp_seconds": origin + 0.04,
                    "end_timestamp_seconds": origin + 0.12,
                    "origin_timestamp_seconds": origin,
                }
            )
        ],
        canonical_timestamps=CanonicalTimestampTransformation(
            before=raw,
            after=snapshot(
                [0, 80, 100, 120] if failure == "outside" else [0, 20, 45, 120], "b"
            ),
        ),
    )
    video.meta = append_canonical_timeline_history(
        video.meta,
        evidence,
        artifact_kind="processed",
        output_content_hash=video.processed_video_hash,
    )
    if failure:
        if failure == "stale_segment":
            segment.start_frame_number = 0
            segment.save()
        with (
            pytest.raises((VideoStorageNormalizationError, ValidationError)),
            transaction.atomic(),
        ):
            publish_processed_coordinates(video, evidence)
        assert list(
            video.frames.order_by("frame_number").values_list("timestamp", flat=True)
        ) == [origin + index * 0.04 for index in range(4)]
        segment.refresh_from_db()
        assert segment.start_frame_number == (0 if failure == "stale_segment" else 1)
        assert segment.end_frame_number == 3
        time_segment.refresh_from_db()
        assert time_segment.start_time == origin + 0.04
        return
    with transaction.atomic():
        publish_processed_coordinates(video, evidence)
        video.save()
    video.refresh_from_db()
    segment.refresh_from_db()
    time_segment.refresh_from_db()
    assert (time_segment.start_time, time_segment.stop_time) == (0.045, 0.12)
    assert (segment.start_frame_number, segment.end_frame_number) == (2, 3)
    assert (segment.start_time, segment.end_time) == (0.045, 0.12)
    assert [row.pk for row in video.frames.order_by("frame_number")] == [
        row.pk for row in frames
    ]
    assert (
        ImageClassificationAnnotation.objects.filter(pk=annotation.pk).values().get()
        == annotation_before
    )
    assert list(timestamp_csv(video, original=True)) == original_export
    assert len(list(timestamp_csv(video, original=False))) == 5
    payload = VideoFileMetaPayload.model_validate(video.meta)
    assert payload.source_timeline is not None
    assert payload.source_timeline.content_hash == video.processed_video_hash
    assert payload.canonical_timeline_history is not None
    assert (
        payload.canonical_timeline_history[-1].segment_mappings[:1]
        == evidence.segment_mappings
    )
    assert (
        payload.canonical_timeline_history[-1].segment_mappings[1].coordinate_kind
        == "video_segmentation_annotation"
    )
    assert payload.canonical_timeline_history[0].before == raw
    video.processed_video_hash = "c" * 64
    with pytest.raises(ValueError, match="another generation"):
        video.frame_number_to_s(1)


def test_duplicate_processed_pts_are_rejected() -> None:
    with pytest.raises(ValidationError):
        snapshot([0, 0, 40], "b")
