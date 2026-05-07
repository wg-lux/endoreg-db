from __future__ import annotations

from lx_ai_core.temporal import (
    binary_markov_smooth_scores,
    normalize_transition_matrix,
    state_path_to_segments,
    sticky_transition_matrix,
    viterbi_decode_state_scores,
)


def test_binary_markov_smoothing_suppresses_isolated_spikes() -> None:
    smoothed, report = binary_markov_smooth_scores(
        [[0.05], [0.90], [0.05], [0.05]],
        ["polyp"],
        stay_probability=0.98,
        enter_probability=0.01,
    )

    assert report["model"] == "binary_markov"
    assert smoothed[1][0] < 0.90
    assert smoothed[2][0] < smoothed[1][0]


def test_binary_markov_smoothing_can_diffuse_on_scene_change() -> None:
    sticky, _ = binary_markov_smooth_scores(
        [[0.95], [0.10]],
        ["outside"],
        stay_probability=0.99,
        enter_probability=0.01,
        change_scores=[0.0, 0.0],
    )
    diffused, report = binary_markov_smooth_scores(
        [[0.95], [0.10]],
        ["outside"],
        stay_probability=0.99,
        enter_probability=0.01,
        change_scores=[0.0, 1.0],
        diffusion_target=0.5,
    )

    assert report["change_scores"] == [0.0, 1.0]
    assert diffused[1][0] < sticky[1][0]


def test_sticky_transition_matrix_is_row_stochastic() -> None:
    matrix = sticky_transition_matrix(3, stay_probability=0.9)

    assert matrix[0][0] == 0.9
    assert all(abs(sum(row) - 1.0) < 1e-9 for row in matrix)


def test_normalize_transition_matrix_repairs_zero_rows() -> None:
    matrix = normalize_transition_matrix([[0.0, 0.0], [2.0, 1.0]])

    assert matrix[0] == [0.5, 0.5]
    assert matrix[1] == [2.0 / 3.0, 1.0 / 3.0]


def test_viterbi_decode_prefers_temporal_consistency() -> None:
    path, confidences, report = viterbi_decode_state_scores(
        [
            [0.95, 0.05],
            [0.40, 0.60],
            [0.95, 0.05],
        ],
        ["mucosa", "polyp"],
        stay_probability=0.98,
    )

    assert report["model"] == "viterbi"
    assert path == [0, 0, 0]
    assert len(confidences) == 3


def test_state_path_to_segments_groups_decoded_states() -> None:
    segments = state_path_to_segments(
        [0, 0, 1, 1, 0],
        [0.9, 0.8, 0.7, 0.6, 0.5],
        ["outside", "mucosa"],
    )

    assert [segment.label for segment in segments] == ["outside", "mucosa", "outside"]
    assert segments[0].start_frame == 0
    assert segments[0].end_frame == 1
    assert segments[1].frame_count == 2
