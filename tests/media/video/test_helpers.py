from endoreg_db.helpers.default_objects import (
    get_default_video_file,
    get_latest_segmentation_model,
    get_default_center,
    get_default_processor,
    get_information_source_prediction,
)
from endoreg_db.models import Label, LabelType, LabelVideoSegment, VideoPredictionMeta

def create_test_label(label_name="polyp", label_type_name="video_segmentation"):
    label_type, _ = LabelType.objects.get_or_create(
        name=label_type_name,
        defaults={"description": "Video segmentation labels"}
    )
    label, _ = Label.objects.get_or_create(name=label_name, label_type=label_type)
    return label

def create_test_video_segment(video=None, label=None, start_frame=10, end_frame=20, prediction_meta=None):
    if video is None:
        video = get_default_video_file()
    if label is None:
        label = create_test_label()

    segment = LabelVideoSegment.objects.create(
        video_file=video,
        label=label,
        start_frame_number=start_frame,
        end_frame_number=end_frame,
        prediction_meta=prediction_meta
    )
    return segment

def setup_realistic_test_data():
    '''
    Creates a realistic test setup with a video, labels, and segments.
    Returns a dictionary with video, labels, segments, and prediction_meta.

    Labels and segments are returned as lists of Label and LabelVideoSegment objects (order: nbi, polyp, snare).
    '''
    video = get_default_video_file()

    frame_count = video.frame_count
    fps = video.fps

    assert frame_count > 3*fps, "Video must have enough frames for testing"

    label_nbi = Label.objects.get(name="nbi") or create_test_label("nbi")
    label_polyp = Label.objects.get(name="polyp") or create_test_label("polyp")
    label_snare = Label.objects.get(name="snare") or create_test_label("snare")
    segment_nbi = create_test_video_segment(video, label_nbi, 0, fps)
    segment_polyp = create_test_video_segment(video, label_polyp, fps, fps+10)
    segment_snare = create_test_video_segment(video, label_snare, 2*fps, 3*fps)
    return {
        "video": video,
        "labels": [label_nbi, label_polyp, label_snare],
        "segments": [segment_nbi, segment_polyp, segment_snare],
    }
