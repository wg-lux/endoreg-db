from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.metadata.model_meta import ModelMeta
    from endoreg_db.services.video_files._ai import VideoFrameScoreResult


type VideoPredictionSequenceMap = dict[str, list[tuple[int, int]]]
type FrameSourceMode = Literal["cache", "stream", "auto"]


def predict_video(
    video: "VideoFile",
    model_meta: "ModelMeta",
    dataset_name: str = "inference_dataset",
    smooth_window_size_s: int = 1,
    binarize_threshold: float = 0.5,
    test_run: bool = False,
    n_test_frames: int = 10,
    return_frame_scores: bool = False,
    frame_source_mode: FrameSourceMode = "stream",
    frame_source_file_type: str = "raw",
) -> "VideoPredictionSequenceMap | VideoFrameScoreResult":
    from ._ai import _predict_video_pipeline

    return _predict_video_pipeline(
        video,
        model_meta=model_meta,
        dataset_name=dataset_name,
        smooth_window_size_s=smooth_window_size_s,
        binarize_threshold=binarize_threshold,
        test_run=test_run,
        n_test_frames=n_test_frames,
        return_frame_scores=return_frame_scores,
        frame_source_mode=frame_source_mode,
        frame_source_file_type=frame_source_file_type,
    )


def extract_text_from_video_frames(
    video: "VideoFile",
    frame_fraction: float = 0.001,
    cap: int = 15,
) -> dict[str, str | None] | None:
    from ._ai import _extract_text_from_video_frames

    return _extract_text_from_video_frames(
        video,
        frame_fraction=frame_fraction,
        cap=cap,
    )
