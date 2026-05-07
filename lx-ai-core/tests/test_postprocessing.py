from __future__ import annotations

from lx_ai_core.postprocessing import (
    _python_mask_rle_encode_flat,
    _python_smooth_scores_1d,
    _python_threshold_runs,
    hysteresis_runs,
    mask_rle_decode,
    mask_rle_encode,
    mask_rle_encode_flat,
    multilabel_uncertainty_scores,
    native_available,
    smooth_scores,
    temporal_segments_from_scores,
    threshold_runs,
)


def test_threshold_runs_extracts_min_length_runs() -> None:
    values = [0.1, 0.6, 0.7, 0.2, 0.8, 0.9]

    assert threshold_runs(values, 0.5, min_length=2) == [(1, 2), (4, 5)]

    if native_available():
        assert threshold_runs(values, 0.5, 2) == _python_threshold_runs(values, 0.5, 2)


def test_smooth_scores_handles_1d_and_2d_scores() -> None:
    assert smooth_scores([0.0, 1.0, 0.0], window=3) == [0.5, 1.0 / 3.0, 0.5]
    assert smooth_scores([[0.0, 1.0], [1.0, 0.0], [0.0, 1.0]], window=3) == [
        [0.5, 0.5],
        [1.0 / 3.0, 2.0 / 3.0],
        [0.5, 0.5],
    ]

    if native_available():
        assert smooth_scores([0.0, 1.0, 0.0], 3) == _python_smooth_scores_1d(
            [0.0, 1.0, 0.0],
            3,
        )


def test_smooth_scores_matches_naive_window_for_larger_input() -> None:
    values = [float(index % 7) for index in range(50)]
    window = 9
    radius = window // 2
    expected = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        expected.append(sum(values[start:end]) / (end - start))

    assert smooth_scores(values, window=window) == expected


def test_mask_rle_round_trip() -> None:
    mask = [[0, 1, 1], [0, 0, 1]]

    counts, shape = mask_rle_encode(mask)
    decoded = mask_rle_decode(counts, shape)

    assert counts == [1, 2, 2, 1]
    assert shape == [2, 3]
    assert decoded == mask

    if native_available():
        assert counts == _python_mask_rle_encode_flat([0, 1, 1, 0, 0, 1])


def test_mask_rle_encode_flat_avoids_nested_mask_shape_walk() -> None:
    counts, shape = mask_rle_encode_flat([0, 1, 1, 0, 0, 1], [2, 3])

    assert counts == [1, 2, 2, 1]
    assert shape == [2, 3]


def test_temporal_segments_from_scores_returns_labelled_segments() -> None:
    segments = temporal_segments_from_scores(
        [[0.1, 0.8], [0.2, 0.9], [0.7, 0.1]],
        ["outside", "inside"],
        threshold=0.5,
        min_length=2,
    )

    assert len(segments) == 1
    assert segments[0].label == "inside"
    assert segments[0].start_frame == 0
    assert segments[0].end_frame == 1


def test_hysteresis_runs_bridge_low_confidence_continuation() -> None:
    runs = hysteresis_runs(
        [0.1, 0.82, 0.45, 0.42, 0.2, 0.81],
        high_threshold=0.8,
        low_threshold=0.4,
        min_length=2,
        max_gap=1,
    )

    assert runs == [(1, 5)]


def test_temporal_segments_support_label_thresholds_and_gap_merging() -> None:
    segments = temporal_segments_from_scores(
        [
            [0.75, 0.10],
            [0.10, 0.10],
            [0.80, 0.35],
            [0.10, 0.40],
        ],
        ["strict", "sensitive"],
        thresholds={"strict": 0.7, "sensitive": 0.3},
        min_length=1,
        max_gap=1,
    )

    by_label = {segment.label: segment for segment in segments}
    assert by_label["strict"].start_frame == 0
    assert by_label["strict"].end_frame == 2
    assert by_label["strict"].peak_score == 0.8
    assert by_label["strict"].frame_count == 3
    assert by_label["sensitive"].start_frame == 2
    assert by_label["sensitive"].end_frame == 3


def test_multilabel_uncertainty_scores_rank_ambiguous_predictions_higher() -> None:
    certain, uncertain = multilabel_uncertainty_scores(
        [
            [0.01, 0.99],
            [0.49, 0.51],
        ]
    )

    assert uncertain["binary_entropy"] > certain["binary_entropy"]
    assert uncertain["margin_uncertainty"] > certain["margin_uncertainty"]
