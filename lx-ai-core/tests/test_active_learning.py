from __future__ import annotations

from lx_ai_core.active_learning import (
    ActiveLearningConfig,
    select_active_learning_frame_indices,
)


def test_selector_returns_temporally_spread_sample_indices() -> None:
    selection = select_active_learning_frame_indices(
        sample_indices=[0, 1, 2, 3, 4],
        frame_ids=[10, 11, 12, 13, 14],
        video_ids=[1, 1, 1, 1, 1],
        frame_numbers=[10, 18, 205, 215, 450],
        probs=[
            [0.50, 0.15],
            [0.51, 0.14],
            [0.49, 0.80],
            [0.48, 0.82],
            [0.52, 0.60],
        ],
        embeddings=[
            [1.0, 0.0],
            [0.99, 0.02],
            [0.0, 1.0],
            [0.02, 0.99],
            [0.7, 0.7],
        ],
        quality_scores=[0.95, 0.92, 0.90, 0.88, 0.97],
        labeled_embeddings=[[1.0, 0.0]],
        class_frequencies=[0.40, 0.05],
        config=ActiveLearningConfig(
            budget=3,
            segment_gap_frames=100,
            temporal_spacing_frames=50,
            max_samples_per_segment=1,
        ),
    )

    assert len(selection.selected_sample_indices) == 3
    assert set(selection.selected_sample_indices) == {0, 2, 4}
    assert set(selection.selected_frame_ids) == {10, 12, 14}
    assert selection.segment_count == 3


def test_selector_filters_low_quality_frames() -> None:
    selection = select_active_learning_frame_indices(
        sample_indices=[0, 1],
        frame_ids=[20, 21],
        video_ids=[2, 2],
        frame_numbers=[10, 220],
        probs=[[0.50, 0.50], [0.50, 0.50]],
        embeddings=[[0.0, 1.0], [1.0, 0.0]],
        quality_scores=[0.10, 0.95],
        class_frequencies=[0.50, 0.50],
        config=ActiveLearningConfig(
            budget=2,
            segment_gap_frames=50,
            temporal_spacing_frames=25,
            min_quality_score=0.35,
        ),
    )

    assert selection.selected_sample_indices == [1]
    assert selection.selected_frame_ids == [21]
