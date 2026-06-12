from __future__ import annotations

# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedImport=false, reportUnusedClass=false

from typing import Protocol

from importlib import import_module
from importlib.util import find_spec
from typing import TYPE_CHECKING

from endoreg_db.models import VideoPredictionMeta
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_temporal_inference import _run_video_temporal_inference


class _MaterializesPredictionSegments(Protocol):
    def materialize_prediction_segments(
        self,
        model_name: str | None = None,
        model: object | None = None,
        model_meta_version: object | None = None,
        delete_frames_after: bool = False,
        ocr_frame_fraction: float = 0.001,
        ocr_cap: int = 10,
        smooth_window_size_s: int = 1,
        binarize_threshold: float = 0.5,
        test_run: bool = False,
        n_test_frames: int = 100,
        **kwargs: object,
    ) -> bool: ...


if TYPE_CHECKING:
    from .test_video_file_extracted import VideoFileModelExtractedTest


def _skip_if_real_inference_runtime_unavailable(
    test: "VideoFileModelExtractedTest",
) -> None:
    if find_spec("lx_ai_core") is None:
        test.skipTest("lx-ai-core is required for real temporal inference")

    try:
        ai_module = import_module("endoreg_db.utils.ai")
        for attribute in (
            "Classifier",
            "InferenceDataset",
            "MultiLabelClassificationNet",
        ):
            getattr(ai_module, attribute)

        postprocess_module = import_module("endoreg_db.utils.ai.postprocess")
        for attribute in (
            "concat_pred_dicts",
            "find_true_pred_sequences",
            "make_smooth_preds",
        ):
            getattr(postprocess_module, attribute)
    except ImportError as exc:
        test.skipTest(f"AI runtime is required for real temporal inference: {exc}")


def _test_temporal_prediction_materialization(
    test: "VideoFileModelExtractedTest",
) -> None:
    video_file = test.video_file

    if isinstance(video_file, VideoFile):
        _skip_if_real_inference_runtime_unavailable(test)
        success = _run_video_temporal_inference(
            video_file.pk,
            model_meta_id=test.ai_model_meta.pk,
            delete_frames_after=True,
            ocr_frame_fraction=0.01,
            ocr_cap=5,
            frame_source_mode="stream",
        )
    else:
        success = video_file.materialize_prediction_segments(delete_frames_after=True)

    test.assertTrue(
        success,
        "Temporal prediction segment materialization failed.",
    )

    video_file.refresh_from_db()
    state = video_file.state
    test.assertIsNotNone(state, "VideoState should exist after temporal prediction")
    assert state is not None
    state.refresh_from_db()

    if isinstance(video_file, VideoFile):
        test.assertIsNotNone(
            video_file.video_meta,
            "VideoMeta should exist after temporal prediction",
        )

        prediction_meta_exists = VideoPredictionMeta.objects.filter(
            video_file=video_file,
            model_meta=test.ai_model_meta,
        ).exists()
        test.assertTrue(
            prediction_meta_exists,
            "VideoPredictionMeta should exist after temporal prediction",
        )

    test.assertTrue(
        state.initial_prediction_completed,
        "State.initial_prediction_completed should be True",
    )
    test.assertTrue(state.lvs_created, "State.lvs_created should be True")
