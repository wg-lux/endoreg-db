# pyright: reportUnusedFunction=false
from __future__ import annotations

from typing import TypedDict

from endoreg_db.helpers.default_objects import get_default_video_file
from endoreg_db.models import Label, LabelType, LabelVideoSegment, VideoFile


class RealisticVideoTestData(TypedDict):
    video: VideoFile
    labels: list[Label]
    segments: list[LabelVideoSegment]


def _as_float(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _frame_count_int(video: object, *, default: int = 100) -> int:
    frame_count = getattr(video, "frame_count", None)
    if frame_count is None:
        return default
    return int(_as_float(frame_count, default=float(default)))


def _positive_fps(video: object, *, default: float = 25.0) -> float:
    fps = getattr(video, "fps", None)
    if fps is None:
        get_fps = getattr(video, "get_fps", None)
        if callable(get_fps):
            fps = get_fps()
    fps_value = _as_float(fps, default=default)
    return fps_value if fps_value > 0 else default


def create_test_label(
    label_name: str = "polyp",
    label_type_name: str = "video_segmentation",
) -> Label:
    label_type, _ = LabelType.objects.get_or_create(
        name=label_type_name,
        defaults={"description": "Video segmentation labels"},
    )
    label, _ = Label.objects.get_or_create(name=label_name, label_type=label_type)
    return label


def create_test_video_segment(
    video: VideoFile | None = None,
    label: Label | None = None,
    start_frame: int = 10,
    end_frame: int = 20,
    prediction_meta: object | None = None,
) -> LabelVideoSegment:
    if video is None:
        video = get_default_video_file()
    if label is None:
        label = create_test_label()

    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        start_frame_number=int(start_frame),
        end_frame_number=int(end_frame),
        prediction_meta=prediction_meta,
    )
    return segment


def _get_or_create_label(name: str) -> Label:
    try:
        return Label.objects.get(name=name)
    except Label.DoesNotExist:
        return create_test_label(name)


def setup_realistic_test_data() -> RealisticVideoTestData:
    """
    Creates a realistic test setup with a video, labels, and segments.

    FPS remains a float. Frame boundaries are concrete integers.
    """
    video = get_default_video_file()

    frame_count = _frame_count_int(video)
    fps = _positive_fps(video)
    frames_per_second = max(1, int(round(fps)))

    assert frame_count > 3 * frames_per_second, (
        "Video must have enough frames for testing"
    )

    label_nbi = _get_or_create_label("nbi")
    label_polyp = _get_or_create_label("polyp")
    label_snare = _get_or_create_label("snare")

    segment_nbi = create_test_video_segment(
        video,
        label_nbi,
        0,
        frames_per_second,
    )
    segment_polyp = create_test_video_segment(
        video,
        label_polyp,
        frames_per_second,
        frames_per_second + 10,
    )
    segment_snare = create_test_video_segment(
        video,
        label_snare,
        2 * frames_per_second,
        3 * frames_per_second,
    )

    return {
        "video": video,
        "labels": [label_nbi, label_polyp, label_snare],
        "segments": [segment_nbi, segment_polyp, segment_snare],
    }
