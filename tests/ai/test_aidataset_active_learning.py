import builtins
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import TypedDict, cast
from unittest.mock import patch

import pytest
import shutil
from pathlib import Path

from django.test import SimpleTestCase

from lx_dtypes.models.contracts.ai_dataset import (
    AIDataSetActiveLearningConfigContract,
    AIDataSetActiveLearningSelectionContract,
)
from endoreg_db.utils.paths import TEST_DATA_ROOT
from endoreg_db.services.aidataset_active_learning import (
    select_active_learning_candidates_locally,
    select_active_learning_frame_indices_from_candidates,
    select_active_learning_frame_indices,
)


class AIDataSetActiveLearningTests(SimpleTestCase):
    TMP_DIR = Path(f"{TEST_DATA_ROOT}data/tests/tmp")

    def setUp(self) -> None:
        if self.TMP_DIR.exists():
            shutil.rmtree(self.TMP_DIR)
        self.TMP_DIR.mkdir(parents=True, exist_ok=True)

    def test_selector_returns_temporally_spread_sample_indices(self) -> None:
        selection = select_active_learning_frame_indices(
            sample_indices=[0, 1, 2, 3, 4],
            frame_ids=[10, 11, 12, 13, 14],
            video_ids=[1, 1, 1, 1, 1],
            frame_numbers=[10, 18, 205, 215, 450],
            timestamps=[0.4, 0.72, 8.2, 8.6, 18.0],
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

    def test_selector_filters_low_quality_frames(self) -> None:
        selection = select_active_learning_frame_indices(
            sample_indices=[0, 1],
            frame_ids=[20, 21],
            video_ids=[2, 2],
            frame_numbers=[10, 220],
            timestamps=[0.4, 8.8],
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

    def test_local_selector_preserves_temporal_ranking(self) -> None:
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

    def test_local_selector_rejects_mismatched_class_frequencies(self) -> None:
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


def _candidate() -> dict[str, object]:
    return {
        "sample_index": 0,
        "frame_id": 10,
        "video_id": 1,
        "frame_number": 100,
        "timestamp": 4.125,
        "probs": [0.5, 0.25],
        "embedding": [1.0, 0.0],
        "quality_score": 0.9,
    }


@pytest.mark.parametrize(
    "selector",
    [
        select_active_learning_candidates_locally,
        select_active_learning_frame_indices_from_candidates,
    ],
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("timestamp", float("nan")),
        ("timestamp", -1.0),
        ("probs", [float("inf"), 0.5]),
        ("probs", [-0.1, 0.5]),
        ("embedding", [float("nan"), 0.0]),
        ("frame_id", True),
        ("sample_index", -1),
        ("video_id", 0),
    ],
)
def test_selectors_reject_invalid_candidates(
    selector: Callable[..., AIDataSetActiveLearningSelectionContract],
    field: str,
    value: object,
) -> None:
    candidate = _candidate()
    candidate[field] = value
    with pytest.raises(ValueError):
        selector([candidate])


@pytest.mark.parametrize(
    "selector",
    [
        select_active_learning_candidates_locally,
        select_active_learning_frame_indices_from_candidates,
    ],
)
@pytest.mark.parametrize(
    "frequencies", [[-0.1, 0.5], [float("nan"), 0.5], [float("inf"), 0.5]]
)
def test_selectors_reject_invalid_frequencies(
    selector: Callable[..., AIDataSetActiveLearningSelectionContract],
    frequencies: list[float],
) -> None:
    with pytest.raises(ValueError, match="finite nonnegative"):
        selector([_candidate()], class_frequencies=frequencies)


@pytest.mark.parametrize(
    "selector",
    [
        select_active_learning_candidates_locally,
        select_active_learning_frame_indices_from_candidates,
    ],
)
def test_selectors_reject_nonfinite_reference_embeddings(
    selector: Callable[..., AIDataSetActiveLearningSelectionContract],
) -> None:
    with pytest.raises(ValueError, match="finite"):
        selector([_candidate()], labeled_embeddings=[[float("inf"), 0.0]])


@pytest.mark.parametrize("identity", ["sample_index", "frame_id", "coordinate"])
def test_duplicate_candidate_identities_are_rejected(identity: str) -> None:
    first = _candidate()
    second = {**first, "sample_index": 1, "frame_id": 11, "frame_number": 200}
    if identity == "coordinate":
        second["frame_number"] = first["frame_number"]
    else:
        second[identity] = first[identity]
    with pytest.raises(ValueError, match="unique"):
        select_active_learning_frame_indices_from_candidates([first, second])


class _ArrayRequest(TypedDict):
    sample_indices: list[int]
    frame_ids: list[int]
    video_ids: list[int]
    frame_numbers: list[int]
    timestamps: list[float]
    probs: list[list[float]]
    embeddings: list[list[float]]
    quality_scores: list[float]


def _array_request() -> _ArrayRequest:
    return _ArrayRequest(
        sample_indices=[0],
        frame_ids=[10],
        video_ids=[1],
        frame_numbers=[100],
        timestamps=[4.125],
        probs=[[0.5, 0.25]],
        embeddings=[[1.0, 0.0]],
        quality_scores=[0.9],
    )


@pytest.mark.parametrize("field", ["frame_ids", "timestamps", "quality_scores"])
@pytest.mark.parametrize("value", [[], [1, 2], None])
def test_optional_arrays_require_exact_lengths_and_values(
    field: str, value: object
) -> None:
    request = cast(dict[str, object], _array_request())
    request[field] = value
    with pytest.raises(ValueError):
        select_active_learning_frame_indices(**cast(_ArrayRequest, request))


def test_authoritative_fractional_timestamp_survives_selector_roundtrip() -> None:
    selection = select_active_learning_frame_indices(**_array_request())
    assert selection.selected_candidates[0].timestamp == 4.125


def test_model_instances_are_revalidated_before_selection() -> None:
    from lx_dtypes.models.contracts.ai_dataset import (
        AIDataSetActiveLearningCandidateContract,
    )

    candidate = AIDataSetActiveLearningCandidateContract.model_validate(_candidate())
    candidate.frame_id = True
    with pytest.raises(ValueError):
        select_active_learning_frame_indices_from_candidates([candidate])


def test_missing_runtime_dependency_does_not_silently_select_locally() -> None:
    original_import = builtins.__import__

    def without_core(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "lx_ai_core.active_learning":
            raise ModuleNotFoundError("missing lx_ai_core", name="lx_ai_core")
        return original_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=without_core):
        with pytest.raises(RuntimeError, match="lx-ai-core is required"):
            select_active_learning_frame_indices_from_candidates([_candidate()])


@pytest.mark.parametrize(
    "change",
    [
        "frame_id",
        "timestamp",
        "probs",
        "duplicate",
        "candidate_count",
        "config",
        "selected_frame_ids",
        "quality_gate",
        "frame_score",
        "segment_id",
    ],
)
def test_external_selection_is_bound_to_request(change: str) -> None:
    selection = select_active_learning_candidates_locally([_candidate()])
    selected = selection.selected_candidates[0]
    if change == "frame_id":
        selected.frame_id = 999
    elif change == "timestamp":
        selected.timestamp = 100.0
    elif change == "probs":
        selected.probs = [0.8, 0.1]
    elif change == "duplicate":
        selection.selected_candidates.append(selected)
        selection.selected_sample_indices.append(selected.sample_index)
        selection.selected_frame_ids.append(selected.frame_id)
    elif change == "candidate_count":
        selection.candidate_count = 20
    elif change == "config":
        selection.config.budget = 100
    elif change == "selected_frame_ids":
        selection.selected_frame_ids = [999]
    elif change == "quality_gate":
        selected.quality_gate = 0.0
    elif change == "frame_score":
        selected.frame_score = float("nan")
    else:
        selected.segment_id = 999
    with patch(
        "lx_ai_core.active_learning.select_active_learning_candidates",
        return_value=selection,
    ):
        with pytest.raises(ValueError):
            select_active_learning_frame_indices_from_candidates([_candidate()])


def test_explicit_local_selector_matches_installed_core() -> None:
    candidates = [
        _candidate(),
        {
            **_candidate(),
            "sample_index": 1,
            "frame_id": 11,
            "frame_number": 500,
            "timestamp": 20.2,
            "embedding": [0.0, 1.0],
        },
    ]
    local = select_active_learning_candidates_locally(candidates)
    external = select_active_learning_frame_indices_from_candidates(candidates)
    assert external.selected_frame_ids == local.selected_frame_ids
    assert external.selected_sample_indices == local.selected_sample_indices
    assert [c.timestamp for c in external.selected_candidates] == [
        c.timestamp for c in local.selected_candidates
    ]
