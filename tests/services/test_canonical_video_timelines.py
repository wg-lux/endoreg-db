from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import (
    CanonicalFrameTimeline,
    CanonicalTimestampTransformation,
    FramePresentationTimestamp,
    VideoArtifactProbe,
)
from endoreg_db.services.video_storage import canonical_timelines as service
from endoreg_db.services.video_storage import workflow
from endoreg_db.services.video_storage.contracts import VideoStorageNormalizationError
from endoreg_db.services.video_storage.validation import validate_normalized_output
from endoreg_db.services.video_storage.workflow import configured_video_storage_profile
from tests.services.test_video_processed_transcode_encryption import probe
from tests.helpers.canonical_timestamps import (
    decoded_test_timestamps,
    normalization_evidence_fixture,
)


def snapshot(ticks: list[int], *, denominator: int = 1000) -> CanonicalFrameTimeline:
    return CanonicalFrameTimeline(
        content_hash="a" * 64,
        time_base_num=1,
        time_base_den=denominator,
        presentation_timestamps=ticks,
    )


@pytest.mark.parametrize("ticks", [[], [-1, 0], [0, 0], [40, 20]])
def test_invalid_frame_sequences_fail(ticks: list[int]) -> None:
    with pytest.raises(ValidationError):
        snapshot(ticks)


@pytest.mark.parametrize("ticks", [[0], [123, 163, 245], [0, 40000, 80000]])
def test_exact_irregular_and_offset_coordinates_round_trip(ticks: list[int]) -> None:
    original = snapshot(ticks)
    assert (
        CanonicalFrameTimeline.model_validate_json(original.model_dump_json())
        == original
    )


def test_resampling_and_later_publication_preserve_both_sequences() -> None:
    before = snapshot([0, 10, 20, 30, 40])
    after = snapshot([0, 200, 400], denominator=10000)
    evidence = validate_normalized_output(
        source=probe(), output=probe(), profile=configured_video_storage_profile()
    ).model_copy(
        update={
            "canonical_timestamps": CanonicalTimestampTransformation(
                before=before, after=after
            )
        }
    )
    first = service.append_canonical_timeline_history(
        None, evidence, artifact_kind="raw", output_content_hash="a" * 64
    )
    second = service.append_canonical_timeline_history(
        first, evidence, artifact_kind="processed", output_content_hash="a" * 64
    )
    first_history = VideoFileMetaPayload.model_validate(
        first
    ).canonical_timeline_history
    assert first_history is not None and len(first_history) == 1
    validated = VideoFileMetaPayload.model_validate(second)
    assert validated.canonical_timeline_history is not None
    assert len(validated.canonical_timeline_history) == 2
    assert validated.canonical_timeline_history[1].before == before
    assert validated.canonical_timeline_history[1].after == after
    assert (
        service.append_canonical_timeline_history(
            second, evidence, artifact_kind="processed", output_content_hash="a" * 64
        )
        == second
    )


def test_publication_rejects_missing_historical_capture() -> None:
    evidence = validate_normalized_output(
        source=probe(), output=probe(), profile=configured_video_storage_profile()
    )
    with pytest.raises(VideoStorageNormalizationError, match="before/after"):
        service.append_canonical_timeline_history(
            None, evidence, artifact_kind="processed", output_content_hash="a" * 64
        )


def test_model_boundary_rejects_invalid_history_and_hls_generation() -> None:
    for kind, ticks in [("hls", [0, 40]), ("processed", [40, 0])]:
        raw = snapshot([0, 40]).model_dump(mode="json")
        raw["presentation_timestamps"] = ticks
        with pytest.raises(ValidationError):
            VideoFileMetaPayload.model_validate(
                {
                    "canonical_timeline_history": [
                        {"artifact_kind": kind, "before": raw, "after": raw}
                    ]
                }
            )


def test_capture_rejects_missing_time_base(tmp_path: Path) -> None:
    with pytest.raises(VideoStorageNormalizationError, match="time base"):
        service.capture_canonical_timeline(
            tmp_path / "missing.mp4",
            probe().model_copy(
                update={
                    "timeline": probe().timeline.model_copy(
                        update={"time_base_num": None, "time_base_den": None}
                    )
                }
            ),
        )


def test_capture_rejects_source_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"before")

    def changed(_path: Path) -> list[FramePresentationTimestamp]:
        path.write_bytes(b"after mutation")
        return [
            FramePresentationTimestamp(
                presentation_timestamp=0, presentation_time_seconds=0.0
            )
        ]

    monkeypatch.setattr(service, "probe_video_frame_timestamps", changed)
    with pytest.raises(VideoStorageNormalizationError, match="changed"):
        service.capture_canonical_timeline(path, probe())


def test_publication_rejects_timestamp_evidence_for_other_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    evidence = normalization_evidence_fixture(source, source)
    with pytest.raises(VideoStorageNormalizationError, match="published content"):
        service.append_canonical_timeline_history(
            None, evidence, artifact_kind="processed", output_content_hash="b" * 64
        )


def test_failed_output_timestamp_scan_preserves_existing_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"previous generation")

    def timestamps(path: Path) -> list[FramePresentationTimestamp]:
        if path != source:
            raise VideoStorageNormalizationError(
                "Missing output presentation timestamp"
            )
        return decoded_test_timestamps(path)

    def artifact_probe(_path: Path) -> VideoArtifactProbe:
        return probe()

    monkeypatch.setattr(workflow, "probe_video_artifact", artifact_probe)
    monkeypatch.setattr(service, "probe_video_frame_timestamps", timestamps)
    with pytest.raises(VideoStorageNormalizationError, match="Missing output"):
        workflow.ensure_video_file_profile(
            input_path=source,
            output_path=output,
            reference_path=source,
            quality_mode="balanced",
        )
    assert output.read_bytes() == b"previous generation"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "output.mp4",
        "source.mp4",
    ]
