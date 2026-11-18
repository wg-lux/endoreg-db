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
    MeasuredAverageFrameRate,
    NominalFrameRate,
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


@pytest.mark.parametrize(
    "after",
    [
        snapshot([123, 163, 245]),
        snapshot([1230, 1630, 2450], denominator=10000),
        snapshot([12, 16, 24], denominator=100),
    ],
)
def test_native_timestamp_equivalence_allows_only_time_base_rounding(
    after: CanonicalFrameTimeline,
) -> None:
    service.assert_canonical_timeline_equivalence(snapshot([123, 163, 245]), after)


@pytest.mark.parametrize(
    "ticks", [[0, 40, 122], [123, 164, 245], [123, 245], [123, 163, 200, 245]]
)
def test_native_timestamp_equivalence_rejects_shift_drop_and_insertion(
    ticks: list[int],
) -> None:
    with pytest.raises(service.VideoStorageNormalizationError, match="Canonical"):
        service.assert_canonical_timeline_equivalence(
            snapshot([123, 163, 245]), snapshot(ticks)
        )


@pytest.mark.parametrize("proof_kind", ["valid", "missing", "shifted", "incomplete"])
@pytest.mark.parametrize("rate_kind", ["nominal", "average", "both"])
def test_rate_metadata_difference_requires_complete_native_proof(
    proof_kind: str,
    rate_kind: str,
) -> None:
    source = probe()
    output = source.model_copy(
        update={
            "timeline": source.timeline.model_copy(
                update={
                    "measured_average_frame_rate": MeasuredAverageFrameRate(
                        numerator=249, denominator=10
                    )
                    if rate_kind in {"average", "both"}
                    else None,
                    "nominal_frame_rate": NominalFrameRate(numerator=30, denominator=1)
                    if rate_kind in {"nominal", "both"}
                    else None,
                }
            )
        }
    )
    ticks = [index * 40 for index in range(250)]
    after_ticks = list(ticks)
    if proof_kind == "shifted":
        after_ticks[10] += 1
    elif proof_kind == "incomplete":
        after_ticks.pop()
    timestamps = (
        None
        if proof_kind == "missing"
        else CanonicalTimestampTransformation(
            before=snapshot(ticks),
            after=snapshot(after_ticks),
        )
    )

    def validate() -> None:
        evidence = validate_normalized_output(
            source=source,
            output=output,
            profile=configured_video_storage_profile(),
            canonical_timestamps=timestamps,
        )
        assert evidence.canonical_timestamps == timestamps

    if proof_kind == "valid":
        validate()
    else:
        with pytest.raises(VideoStorageNormalizationError):
            validate()


def test_decoded_proof_does_not_override_terminal_duration() -> None:
    source = probe()
    output = source.model_copy(
        update={
            "timeline": source.timeline.model_copy(update={"duration_seconds": 12.0})
        }
    )
    timestamps = snapshot([index * 40 for index in range(250)])
    with pytest.raises(VideoStorageNormalizationError, match="duration drifted"):
        validate_normalized_output(
            source=source,
            output=output,
            profile=configured_video_storage_profile(),
            canonical_timestamps=CanonicalTimestampTransformation(
                before=timestamps, after=timestamps
            ),
        )


def test_rate_metadata_drift_reuses_compliant_video_on_repeated_normalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"existing canonical generation")
    original_stat = output.stat()
    source_probe = probe()
    output_probe = source_probe.model_copy(
        update={
            "timeline": source_probe.timeline.model_copy(
                update={
                    "nominal_frame_rate": NominalFrameRate(numerator=30, denominator=1),
                    "measured_average_frame_rate": MeasuredAverageFrameRate(
                        numerator=249, denominator=10
                    ),
                }
            )
        }
    )

    def artifact_probe(path: Path) -> VideoArtifactProbe:
        return source_probe if path == source else output_probe

    def unexpected_transcode(**_kwargs: object) -> None:
        pytest.fail("Matching decoded timestamps must not cause another encode")

    monkeypatch.setattr(workflow, "probe_video_artifact", artifact_probe)
    monkeypatch.setattr(
        service, "probe_video_frame_timestamps", decoded_test_timestamps
    )
    monkeypatch.setattr(
        workflow.ffmpeg_wrapper, "transcode_video", unexpected_transcode
    )
    for _ in range(2):
        evidence = workflow.normalize_video_file(
            input_path=output,
            reference_path=source,
            quality_mode="balanced",
        )
        assert evidence.canonical_timestamps is not None
        assert evidence.temporal_equivalent
        assert output.stat().st_ino == original_stat.st_ino
        assert output.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert output.read_bytes() == b"existing canonical generation"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "output.mp4",
        "source.mp4",
    ]


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
