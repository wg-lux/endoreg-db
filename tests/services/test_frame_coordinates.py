from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock
from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject

import numpy as np
import pytest

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.video_meta import FFMpegMeta, VideoMeta
from endoreg_db.services.video_files import frame_coordinates as service
from endoreg_db.utils.ai.postprocess import make_smooth_preds
from endoreg_db.services.video_files.metadata import get_video_duration


def _video() -> VideoFile:
    def snapshot(digest: str, ticks: list[int]) -> dict[str, object]:
        return dict(
            content_hash=digest,
            time_base_num=1,
            time_base_den=1000,
            presentation_timestamps=ticks,
        )

    raw = snapshot("a" * 64, [2000, 2040, 2200])
    processed = snapshot("b" * 64, [3000, 3040, 3200])
    return VideoFile(
        raw_video_hash="a" * 64,
        processed_video_hash="b" * 64,
        meta={
            "canonical_timeline_history": [
                {"artifact_kind": "raw", "before": raw, "after": raw},
                {"artifact_kind": "processed", "before": raw, "after": processed},
            ],
        },
    )


@pytest.mark.parametrize(
    "kind, digest, expected",
    [
        ("raw", "a" * 64, (2.0, 2.04, 2.2)),
        ("processed", "b" * 64, (3.0, 3.04, 3.2)),
    ],
)
def test_selected_generation_uses_its_own_origin(
    monkeypatch: pytest.MonkeyPatch, kind: str, digest: str, expected: tuple[float, ...]
) -> None:
    def verified_hash(field: object) -> str:
        return digest

    monkeypatch.setattr(service, "verified_video_source_hash", verified_hash)
    assert service.artifact_frame_timestamps(_video(), file_type=kind) == expected


@pytest.mark.parametrize("kind", ["raw", "processed"])
def test_replaced_generation_is_rejected(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    def verified_hash(field: object) -> str:
        return "c" * 64

    monkeypatch.setattr(service, "verified_video_source_hash", verified_hash)
    with pytest.raises(ValueError):
        service.artifact_frame_timestamps(_video(), file_type=kind)


@pytest.mark.parametrize("receipt_kind", ["verified", "unresolved"])
@pytest.mark.parametrize(
    "history", ["missing", "matching", "conflicting", "stale", "wrong_recording"]
)
def test_received_processed_coordinates_require_current_unambiguous_evidence(
    monkeypatch: pytest.MonkeyPatch, receipt_kind: str, history: str
) -> None:
    from lx_dtypes.models.contracts.video_provenance import (
        VideoFrameProvenancePayload,
        VideoProvenanceBundle,
        VideoProvenanceRecovery,
    )
    from endoreg_db.schemas.persisted_json import VideoFileMetaPayload

    video = _video()
    entries = VideoFileMetaPayload.model_validate(video.meta).canonical_timeline_history
    assert entries
    raw, processed = entries[0].before, entries[1].after
    metadata: VideoFileMetaJsonObject = {}
    if history == "stale":
        processed = processed.model_copy(update={"content_hash": "c" * 64})
    if receipt_kind == "verified":
        value = VideoProvenanceBundle(
            transformations=[
                VideoFrameProvenancePayload(
                    source=raw,
                    output=processed,
                    source_frame_indices=[0, 1, 2],
                )
            ]
        )
        metadata["video_frame_provenance"] = value.model_dump(mode="json")
    else:
        recovery = VideoProvenanceRecovery(
            recording_hash=video.raw_video_hash,
            processed_timeline=processed,
        )
        metadata["video_provenance_recovery"] = recovery.model_dump(mode="json")
    if history in {"matching", "conflicting"}:
        snapshot = entries[1].after.model_dump(mode="json")
        if history == "conflicting":
            snapshot["presentation_timestamps"] = [3000, 3050, 3200]
        metadata["canonical_timeline_history"] = [
            {"artifact_kind": "processed", "before": snapshot, "after": snapshot}
        ]
    video.meta = metadata
    if history == "wrong_recording":
        video.raw_video_hash = "d" * 64

    def verified_hash(_field: object) -> str:
        return "b" * 64

    monkeypatch.setattr(service, "verified_video_source_hash", verified_hash)
    if history in {"conflicting", "stale", "wrong_recording"}:
        with pytest.raises(ValueError, match="unambiguous|another recording"):
            service.artifact_frame_timestamps(video, file_type="processed")
    else:
        assert service.artifact_frame_timestamps(video, file_type="processed") == (
            3.0,
            3.04,
            3.2,
        )
    with pytest.raises(ValueError, match="unambiguous"):
        service.artifact_frame_timestamps(video, file_type="raw")


def test_missing_canonical_coordinates_are_not_inferred_from_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video()
    video.meta = {}
    video.fps = 25
    video.frame_count = 3

    def verified_hash(_field: object) -> str:
        return "b" * 64

    monkeypatch.setattr(service, "verified_video_source_hash", verified_hash)
    with pytest.raises(ValueError, match="unambiguous"):
        service.artifact_frame_timestamps(video, file_type="processed")


def test_invalid_artifact_kind_is_not_raw() -> None:
    with pytest.raises(ValueError):
        service.artifact_frame_timestamps(_video(), file_type="unknown")


def test_centered_smoothing_preserves_irregular_frame_positions() -> None:
    np.testing.assert_allclose(
        make_smooth_preds([0.0, 1.0, 0.0, 1.0], 0.2, timestamps=[2.0, 2.04, 2.3, 2.31]),
        [0.5, 0.5, 0.5, 0.5],
    )


@pytest.mark.parametrize(
    "times", [[0.0], [0.0, 0.0], [1.0, 0.0], [0.0, float("nan")], [-1.0, 0.0]]
)
def test_invalid_smoothing_coordinates_reject(times: list[float]) -> None:
    with pytest.raises(ValueError):
        make_smooth_preds([0.0, 1.0], 0.2, timestamps=times)


def test_segment_duration_uses_presentation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boundaries(video: VideoFile, count: int) -> tuple[float, ...]:
        assert count == 4
        return (2.0, 2.04, 2.3, 2.31, 2.4)

    monkeypatch.setattr(service, "prediction_frame_boundaries", boundaries)
    values = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    assert service.prediction_segments(_video(), values, 0.2) == [[(0, 2)], []]


def test_missing_prediction_coordinates_reject() -> None:
    frames = Mock()
    frames.order_by.return_value.values_list.return_value = [(0, None)]
    video = cast(VideoFile, SimpleNamespace(frames=frames))
    with pytest.raises(ValueError, match="complete persisted"):
        service.prediction_frame_boundaries(video, 1)


def test_duration_does_not_reconstruct_from_rate() -> None:
    video = VideoFile(duration=1.23, fps=99, frame_count=100, meta={})
    assert get_video_duration(video) == 1.23
    video.duration = None
    with pytest.raises(ValueError, match="persisted"):
        get_video_duration(video)


@pytest.mark.parametrize(
    "observed, expected", [("37", 37), ("N/A", None), (None, None)]
)
def test_metadata_frame_count_is_observed(
    observed: str | None, expected: int | None
) -> None:
    metadata = VideoMeta(
        ffmpeg_meta=FFMpegMeta(
            duration=100.0,
            frame_rate_num=25,
            frame_rate_den=1,
            raw_probe_data={
                "streams": [{"codec_type": "video", "nb_frames": observed}]
            },
        )
    )
    assert metadata.frame_count == expected
