# pyright: reportPrivateUsage=false
from unittest.mock import Mock

import numpy as np
import pytest

from endoreg_db.models.metadata import video_prediction_meta as prediction_module
from endoreg_db.models.metadata.video_prediction_meta import (
    VideoPredictionMeta,
    _confidence_array_from_predictions,
)


def _prediction_str(prediction_meta: VideoPredictionMeta) -> str:
    return "prediction"


def _constant_fps(video: object) -> float:
    return 2.0


def test_confidence_array_ignores_out_of_bounds_predictions() -> None:
    confidences = _confidence_array_from_predictions(
        [(-1, 0.1), (1, 0.8), (3, 0.9)],
        num_frames=3,
        label_name="lesion",
        video_obj="video",
    )

    np.testing.assert_array_equal(confidences, np.array([0.5, 0.8, 0.5]))


def test_create_video_segments_delegates_each_non_empty_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_label = Mock(name="first_label")
    second_label = Mock(name="second_label")
    prediction_array = np.array(
        [
            [0.0, 1.0],
            [1.0, 1.0],
            [1.0, 0.0],
        ],
        dtype=np.float64,
    )
    prediction_meta = VideoPredictionMeta()
    create_for_label = Mock()

    monkeypatch.setattr(VideoPredictionMeta, "__str__", _prediction_str)
    monkeypatch.setattr(prediction_meta, "get_video", lambda: "video")
    monkeypatch.setattr(
        prediction_meta,
        "get_label_list",
        lambda: [first_label, second_label],
    )
    monkeypatch.setattr(
        prediction_meta,
        "_get_or_calculate_prediction_array",
        lambda: prediction_array,
    )
    monkeypatch.setattr(
        prediction_meta,
        "create_video_segments_for_label",
        create_for_label,
    )
    monkeypatch.setattr(prediction_module, "get_video_fps", _constant_fps)
    find_segments = Mock(side_effect=[[(1, 2)], []])
    monkeypatch.setattr(
        prediction_module,
        "find_segments_in_prediction_array",
        find_segments,
    )

    prediction_meta.create_video_segments(segment_length_threshold_in_s=1.0)

    assert find_segments.call_count == 2
    assert all(call.args[1] == 2 for call in find_segments.call_args_list)
    create_for_label.assert_called_once_with([(1, 2)], first_label)
