import shutil
from pathlib import Path

from django.test import SimpleTestCase

from endoreg_db.models.aidataset.aidataset import (
    AIDataSet,
    AIDataSetActiveLearningConfigContract,
)
from endoreg_db.services.aidataset_active_learning import (
    select_active_learning_candidates_locally,
)


class AIDataSetActiveLearningTests(SimpleTestCase):
    TMP_DIR = Path("/home/admin/endoreg-db/data/tests/tmp")

    def setUp(self):
        if self.TMP_DIR.exists():
            shutil.rmtree(self.TMP_DIR)
        self.TMP_DIR.mkdir(parents=True, exist_ok=True)

    def test_selector_returns_temporally_spread_sample_indices(self):
        selection = AIDataSet.select_active_learning_frame_indices(
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
            config=AIDataSetActiveLearningConfigContract(
                budget=3,
                segment_gap_frames=100,
                temporal_spacing_frames=50,
                max_samples_per_segment=1,
            ),
        )

        self.assertEqual(len(selection.selected_sample_indices), 3)
        self.assertEqual(set(selection.selected_sample_indices), {0, 2, 4})
        self.assertEqual(set(selection.selected_frame_ids), {10, 12, 14})
        self.assertEqual(selection.segment_count, 3)

    def test_selector_filters_low_quality_frames(self):
        selection = AIDataSet.select_active_learning_frame_indices(
            sample_indices=[0, 1],
            frame_ids=[20, 21],
            video_ids=[2, 2],
            frame_numbers=[10, 220],
            probs=[[0.50, 0.50], [0.50, 0.50]],
            embeddings=[[0.0, 1.0], [1.0, 0.0]],
            quality_scores=[0.10, 0.95],
            class_frequencies=[0.50, 0.50],
            config=AIDataSetActiveLearningConfigContract(
                budget=2,
                segment_gap_frames=50,
                temporal_spacing_frames=25,
                min_quality_score=0.35,
            ),
        )

        self.assertEqual(selection.selected_sample_indices, [1])
        self.assertEqual(selection.selected_frame_ids, [21])

    def test_local_selector_preserves_temporal_ranking(self):
        candidates = [
            {
                "sample_index": sample_index,
                "frame_id": frame_id,
                "video_id": 1,
                "frame_number": frame_number,
                "timestamp": float(frame_number),
                "probs": probabilities,
                "embedding": embedding,
                "quality_score": quality_score,
            }
            for (
                sample_index,
                frame_id,
                frame_number,
                probabilities,
                embedding,
                quality_score,
            ) in zip(
                [0, 1, 2, 3, 4],
                [10, 11, 12, 13, 14],
                [10, 18, 205, 215, 450],
                [
                    [0.50, 0.15],
                    [0.51, 0.14],
                    [0.49, 0.80],
                    [0.48, 0.82],
                    [0.52, 0.60],
                ],
                [
                    [1.0, 0.0],
                    [0.99, 0.02],
                    [0.0, 1.0],
                    [0.02, 0.99],
                    [0.7, 0.7],
                ],
                [0.95, 0.92, 0.90, 0.88, 0.97],
                strict=True,
            )
        ]

        selection = select_active_learning_candidates_locally(
            candidates,
            labeled_embeddings=[[1.0, 0.0]],
            class_frequencies=[0.40, 0.05],
            config=AIDataSetActiveLearningConfigContract(
                budget=3,
                segment_gap_frames=100,
                temporal_spacing_frames=50,
                max_samples_per_segment=1,
            ),
        )

        self.assertEqual(selection.selected_sample_indices, [2, 4, 0])
        self.assertEqual(selection.selected_frame_ids, [12, 14, 10])
        self.assertEqual(selection.segment_count, 3)

    def test_local_selector_rejects_mismatched_class_frequencies(self):
        with self.assertRaisesRegex(
            ValueError,
            "class_frequencies must match",
        ):
            select_active_learning_candidates_locally(
                [
                    {
                        "sample_index": 0,
                        "frame_id": 10,
                        "video_id": 1,
                        "frame_number": 10,
                        "timestamp": 10.0,
                        "probs": [0.5, 0.5],
                        "embedding": [1.0, 0.0],
                        "quality_score": 0.9,
                    }
                ],
                class_frequencies=[0.5],
            )
