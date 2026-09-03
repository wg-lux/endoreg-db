from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    AiModel,
    Center,
    Frame,
    InformationSource,
    Label,
    LabelSet,
    LabelVideoSegment,
    ModelMeta,
    VideoFile,
    VideoPredictionMeta,
)
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_files._segments import convert_sequences_to_db_segments
from endoreg_db.services.video_storage_normalization import evidence_as_json
from endoreg_db.views.video.segments_crud import (
    PREDICTION_CORRECTION_SOURCE_NAME,
    import_prediction_segments_to_manual,
    video_segments_validate_bulk,
)


pytestmark = pytest.mark.django_db


def _video(*, suffix: str, frame_count: int = 20, fps: float = 25.0) -> VideoFile:
    return VideoFile.objects.create(
        center=Center.objects.create(name=f"ai-segment-acceptance-center-{suffix}"),
        video_hash=f"ai-segment-acceptance-video-{suffix}",
        original_file_name=f"ai-segment-acceptance-{suffix}.mp4",
        frame_count=frame_count,
        fps=fps,
    )


def test_prediction_materialization_is_all_or_nothing() -> None:
    video = _video(suffix="materialization")
    known_label = Label.objects.create(name="acceptance-known-label")
    label_set = LabelSet.objects.create(
        name="acceptance-materialization-label-set",
        version=1,
    )
    label_set.labels.add(known_label)
    ai_model = AiModel.objects.create(name="acceptance-materialization-model")
    model_meta = ModelMeta.objects.create(
        name="acceptance-materialization-meta",
        version="1",
        model=ai_model,
        labelset=label_set,
    )
    prediction_meta = VideoPredictionMeta.objects.create(
        video_file=video,
        model_meta=model_meta,
    )

    with pytest.raises(RuntimeError, match="materializ"):
        convert_sequences_to_db_segments(
            video=video,
            sequences={
                known_label.name: [(0, 5)],
                "acceptance-unknown-model-label": [(5, 10)],
            },
            video_prediction_meta=prediction_meta,
        )

    assert not LabelVideoSegment.objects.filter(video_file=video).exists()


def test_prediction_correction_import_rolls_back_the_complete_batch() -> None:
    video = _video(suffix="atomic-import")
    label = Label.objects.create(name="acceptance-atomic-label")
    correction_source = InformationSource.objects.create(
        name=PREDICTION_CORRECTION_SOURCE_NAME
    )
    manual_source = InformationSource.objects.create(name="manual_annotation")
    existing = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        source=correction_source,
        start_frame_number=1,
        end_frame_number=4,
    )

    class _LateFailureSerializer:
        calls = 0

        def __init__(self, *, data: object) -> None:
            self.data = data
            self.errors = {"end_time": ["late batch validation failure"]}
            self.call_index = _LateFailureSerializer.calls
            _LateFailureSerializer.calls += 1

        def is_valid(self) -> bool:
            return self.call_index == 0

        def save(self) -> LabelVideoSegment:
            return LabelVideoSegment.objects.create(
                video_file=video,
                label=label,
                source=manual_source,
                start_frame_number=5,
                end_frame_number=8,
            )

    request = APIRequestFactory().post(
        f"/api/media/videos/{video.pk}/segments/import-predictions/",
        {
            "replace_existing": True,
            "segments": [
                {"label_name": label.name, "start_time": 0.2, "end_time": 0.3},
                {"label_name": label.name, "start_time": 0.4, "end_time": 0.5},
            ],
        },
        format="json",
    )

    with (
        patch(
            "endoreg_db.views.video.segments_crud.LabelVideoSegmentSerializer",
            _LateFailureSerializer,
        ),
        patch("endoreg_db.views.video.segments_crud._sync_frame_annotations"),
        patch(
            "endoreg_db.views.video.segments_crud._delete_frame_annotations_for_segment"
        ),
    ):
        response = import_prediction_segments_to_manual(request, pk=int(video.pk))

    assert response.status_code == 400
    assert LabelVideoSegment.objects.filter(pk=existing.pk).exists()
    assert list(
        LabelVideoSegment.objects.filter(video_file=video).values_list(
            "start_frame_number", "end_frame_number"
        )
    ) == [(1, 4)]


def _vfr_video() -> VideoFile:
    timeline = VideoTimelineContract(
        fps_num=25,
        fps_den=1,
        duration_seconds=0.3,
        frame_count=5,
        variable_frame_rate=True,
        time_base_num=1,
        time_base_den=90_000,
    )
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=VideoArtifactProbe(
            codec_name="h264",
            pixel_format="yuv420p",
            width=1280,
            height=720,
            bit_rate_bps=800_000,
            size_bytes=1_000_000,
            timeline=timeline,
        ),
        timestamp_mapping="ffprobe_pts",
    )
    video = VideoFile.objects.create(
        center=Center.objects.create(name="ai-segment-acceptance-vfr-center"),
        video_hash="ai-segment-acceptance-vfr-video",
        fps=25.0,
        duration=0.3,
        frame_count=5,
        meta={"source_timeline": evidence_as_json(evidence)},
    )
    Frame.objects.bulk_create(
        Frame(
            video=video,
            frame_number=frame_number,
            relative_path=f"frame_{frame_number:07d}.jpg",
            timestamp=timestamp,
        )
        for frame_number, timestamp in enumerate((0.0, 0.04, 0.11, 0.16, 0.24))
    )
    return video


def test_segment_validation_preserves_vfr_presentation_timestamp_boundaries() -> None:
    video = _vfr_video()
    label = Label.objects.create(name="acceptance-vfr-label")
    source = InformationSource.objects.create(name="acceptance-vfr-manual")
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        source=source,
        start_frame_number=1,
        end_frame_number=4,
    )
    request = APIRequestFactory().post(
        f"/api/media/videos/{video.pk}/segments/validate-bulk/",
        {
            "segment_ids": [segment.pk],
            "segments": [
                {
                    "id": segment.pk,
                    "start_time": 0.12,
                    "end_time": 0.16,
                }
            ],
            "is_validated": False,
            "information_source_name": "manual_annotation",
        },
        format="json",
    )

    response = video_segments_validate_bulk(request, pk=int(video.pk))

    assert response.status_code == 200
    segment.refresh_from_db()
    assert (segment.start_frame_number, segment.end_frame_number) == (2, 3)
