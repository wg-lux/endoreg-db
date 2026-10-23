# pyright: reportPrivateUsage=false
from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path
from unittest.mock import Mock

import pytest
from lx_dtypes.models.contracts.video_provenance import (
    ElectricalTimestampSeries,
    RecordingSynchronization,
    SynchronizationAnchor,
    VideoFrameProvenancePayload,
    VideoFrameTimeline,
    VideoProvenanceBundle,
)
from pydantic import ValidationError

from endoreg_db.models.media.video.frame_provenance import VideoFrameProvenance
from endoreg_db.services.hub.video_provenance import electrical_sample_indices
from endoreg_db.services.video_storage.frame_provenance import (
    persist_frame_provenance,
    provenance_chain,
    read_encoder_provenance,
)
from endoreg_db.services.video_storage import probes
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.transcode_execution import (
    _run_ffmpeg_command,
)
from endoreg_db.utils.transcode_execution import (
    get_stream_info as real_stream_info,
)
from endoreg_db.utils.video.executable_discovery import resolve_ffmpeg_executable


@pytest.mark.no_db
@pytest.mark.ffmpeg
@pytest.mark.parametrize("irregular", [False, True])
@pytest.mark.parametrize("origin", [0, 17])
def test_parallel_timestamp_probes_preserve_complete_decoded_sequence(
    tmp_path: Path, irregular: bool, origin: int
) -> None:
    ffmpeg = resolve_ffmpeg_executable()
    ffprobe = ffmpeg_wrapper.resolve_ffprobe_executable()
    assert ffmpeg is not None and ffprobe is not None
    source = tmp_path / "timestamps.mp4"
    cadence = "N+floor(N/3)" if irregular else "N"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=60:duration=1",
            "-vf",
            f"setpts=({cadence})/60/TB+{origin}/TB",
            "-fps_mode",
            "passthrough",
            "-enc_time_base",
            "demux",
            "-c:v",
            "libx264",
            "-bf",
            "3",
            "-an",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    for build_command in (
        probes._build_frame_timestamp_probe_command,
        probes._build_streaming_frame_timestamp_probe_command,
    ):
        parallel = build_command(ffprobe, source)
        serial = parallel.copy()
        serial[serial.index("-threads") + 1] = "1"
        expected = subprocess.run(
            serial, check=True, capture_output=True, text=True, timeout=30
        ).stdout
        actual = subprocess.run(
            parallel, check=True, capture_output=True, text=True, timeout=30
        ).stdout
        assert actual == expected
    frames = probes.probe_video_frame_timestamps(source, require_original_pts=True)
    assert len(frames) == 60
    assert frames[0].presentation_time_seconds == origin
    assert all(
        before.presentation_timestamp < after.presentation_timestamp
        for before, after in zip(frames, frames[1:])
    )


def timeline(digest: str, ticks: list[int]) -> VideoFrameTimeline:
    return VideoFrameTimeline(
        content_hash=digest * 64,
        time_base_num=1,
        time_base_den=1000,
        presentation_timestamps=ticks,
    )


def proof() -> VideoFrameProvenancePayload:
    return VideoFrameProvenancePayload(
        source=timeline("a", [100, 120, 150, 180]),
        output=timeline("b", [0, 40, 80]),
        source_frame_indices=[0, 2, 3],
    )


def synchronized_bundle() -> VideoProvenanceBundle:
    return VideoProvenanceBundle(
        transformations=[proof()],
        synchronization=RecordingSynchronization(
            session_id="1" * 32,
            video_clock_id="2" * 32,
            electrical_clock_id="3" * 32,
            source_content_hash="a" * 64,
            electrical_time_base_num=1,
            electrical_time_base_den=1000,
            method="shared_hardware_event",
            uncertainty_num=1,
            uncertainty_den=1000,
            anchors=[
                SynchronizationAnchor(video_timestamp=100, electrical_timestamp=1000),
                SynchronizationAnchor(video_timestamp=180, electrical_timestamp=1160),
            ],
        ),
    )


@pytest.mark.parametrize(
    "indices", [[0, 2], [0, -1, 3], [0, 2, 4], [2, 1, 3], [False, 2, 3]]
)
def test_invalid_output_source_indices_fail(indices: list[int]) -> None:
    with pytest.raises(ValidationError):
        VideoFrameProvenancePayload(
            source=proof().source, output=proof().output, source_frame_indices=indices
        )


def test_no_nearest_timestamp_guess_and_round_trip(tmp_path: Path) -> None:
    stats = tmp_path / "encoder.txt"
    stats.write_text(
        "0 0 0 1/1000 100 1/1000\n1 2 40 1/1000 150 1/1000\n2 3 80 1/1000 180 1/1000\n"
    )
    captured = read_encoder_provenance(
        stats, source=proof().source, output=proof().output
    )
    assert captured.source_frame_indices == [0, 2, 3]
    assert (
        VideoFrameProvenancePayload.model_validate_json(captured.model_dump_json())
        == proof()
    )


@pytest.mark.parametrize(
    "row",
    [
        "0 -1 0 1/1000 100 1/1000",
        "0 0 0 1/1000 101 1/1000",
        "0 0 1 1/1000 100 1/1000",
        "0 0 0 1/1000 9223372036854775807 0/1",
        "1 0 0 1/1000 100 1/1000",
    ],
)
def test_corrupt_encoder_provenance_fails(tmp_path: Path, row: str) -> None:
    stats = tmp_path / "encoder.txt"
    stats.write_text(row + "\n")
    with pytest.raises(ValueError):
        read_encoder_provenance(stats, source=proof().source, output=proof().output)


@pytest.mark.django_db
def test_persistence_is_idempotent_and_conflicts_fail() -> None:
    captured = proof()
    persist_frame_provenance(captured)
    persist_frame_provenance(captured)
    assert VideoFrameProvenance.objects.count() == 1
    assert provenance_chain(
        source_hash="a" * 64, output_hash="b" * 64
    ).transformations == [captured]
    with pytest.raises(ValueError, match="Conflicting"):
        persist_frame_provenance(
            captured.model_copy(update={"source_frame_indices": [0, 1, 3]})
        )
    row = VideoFrameProvenance.objects.get()
    with pytest.raises(ValueError, match="immutable"):
        row.save()


def test_electrical_alignment_uses_selected_source_frames_and_measured_drift() -> None:
    bundle = synchronized_bundle()
    assert electrical_sample_indices(
        bundle,
        samples=ElectricalTimestampSeries(
            session_id="1" * 32,
            clock_id="3" * 32,
            time_base_num=1,
            time_base_den=1000,
            presentation_timestamps=[1000, 1040, 1100, 1160],
        ),
        max_error_seconds=Fraction(1, 1000),
    ) == [0, 2, 3]


@pytest.mark.parametrize(
    "failure",
    ["session", "clock", "unsynchronized", "budget", "outside", "ties", "reset"],
)
def test_electrical_alignment_rejects_unproven_associations(failure: str) -> None:
    bundle = synchronized_bundle()
    samples = [1000, 1040, 1100, 1160]
    if failure == "unsynchronized":
        bundle = VideoProvenanceBundle(transformations=bundle.transformations)
    if failure == "outside":
        samples = [1040, 1100, 1160]
    if failure == "ties":
        samples = [1000, 1090, 1110, 1160]
    if failure == "reset":
        samples = [1000, 1100, 900]
    with pytest.raises(ValueError):
        electrical_sample_indices(
            bundle,
            samples=ElectricalTimestampSeries(
                session_id=("4" if failure == "session" else "1") * 32,
                clock_id=("4" if failure == "clock" else "3") * 32,
                time_base_num=1,
                time_base_den=1000,
                presentation_timestamps=samples,
            ),
            max_error_seconds=Fraction(0 if failure == "budget" else 1, 1),
        )


@pytest.mark.django_db
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    ("origin", "copy_timestamps"), [(0, True), (17, True), (17, False)]
)
def test_real_resampling_keeps_actual_pixel_ancestry(
    tmp_path: Path, origin: int, copy_timestamps: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_wrapper, "get_stream_info", real_stream_info)
    ffmpeg = resolve_ffmpeg_executable()
    assert ffmpeg is not None
    source, output = tmp_path / "source.nut", tmp_path / "output.nut"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=size=16x16:rate=60:duration=1,geq=lum='N*3':cb=128:cr=128",
            "-vf",
            f"setpts=PTS+{origin}/TB",
            "-c:v",
            "ffv1",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    result = _run_ffmpeg_command(
        [
            ffmpeg,
            "-v",
            "error",
            *(["-copyts"] if copy_timestamps else []),
            "-i",
            str(source),
            "-vf",
            "fps=50",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "ffv1",
            str(output),
        ]
    )
    assert result[0] == 0, result[1]
    captured = VideoFrameProvenancePayload.model_validate(
        VideoFrameProvenance.objects.get().payload
    )
    assert len(captured.source.presentation_timestamps) == 60
    assert len(captured.source_frame_indices) == 50

    def pixels(path: Path) -> list[bytes]:
        raw = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(path),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "yuv420p",
                "pipe:1",
            ],
            check=True,
            capture_output=True,
        ).stdout
        return [raw[index : index + 384] for index in range(0, len(raw), 384)]

    source_pixels, output_pixels = pixels(source), pixels(output)
    assert output_pixels == [
        source_pixels[index] for index in captured.source_frame_indices
    ]
    assert not list(tmp_path.glob("*provenance*"))


@pytest.mark.django_db
@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "operation", ["mask", "mask_full", "mask_offset", "detector", "remove"]
)
def test_library_cleaner_records_each_real_encoding_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    from lx_anonymizer.frame_cleaner import FrameCleaner
    from lx_anonymizer.runtime_types import VideoExecutionHooks
    from lx_anonymizer.video_processing.video_encoder import VideoEncoder
    from lx_dtypes.models.contracts.video_processing import (
        VideoEncoderConfig,
        VideoMaskConfig,
    )

    from endoreg_db.services.video_storage import provenance_cleaner as adapter

    monkeypatch.setattr(VideoEncoder, "_detect_nvenc_support", Mock(return_value=False))

    monkeypatch.setattr(ffmpeg_wrapper, "get_stream_info", real_stream_info)
    ffmpeg = resolve_ffmpeg_executable()
    assert ffmpeg is not None
    source, output = tmp_path / "input.mp4", tmp_path / "masked.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=10:duration=1",
            "-vf",
            "setpts=PTS+3/TB" if operation == "mask_offset" else "null",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    cleaner = FrameCleaner.__new__(FrameCleaner)
    cleaner.execution_hooks = VideoExecutionHooks(
        run_encoder=adapter._run_encoder,
        observe_decoded_frames=adapter._observe_decoded_frames,
    )
    cleaner.preferred_encoder = VideoEncoderConfig(
        name="libx264",
        preset_param="-preset",
        preset_value="fast",
        quality_param="-crf",
        quality_value="23",
        type="cpu",
        fallback_preset="fast",
    )
    if operation in {"mask", "mask_full", "mask_offset"}:
        cleaner._init_masking()
        masker = cleaner.mask_application
        assert masker is not None
        config = VideoMaskConfig(
            x=0 if operation == "mask_full" else 8,
            y=0 if operation == "mask_full" else 8,
            width=64 if operation == "mask_full" else 48,
            height=64 if operation == "mask_full" else 48,
            image_width=64,
            image_height=64,
        )
        assert masker.mask_video_streaming(source, config.model_dump(), output)
    else:
        if operation == "detector":
            cleaner.region_detector = None

            def detect_regions(*_args: object) -> list[tuple[int, int, int, int]]:
                return [(0, 0, 8, 8)]

            monkeypatch.setattr(
                "lx_anonymizer.frame_cleaner.cleaner.detect_phi_regions", detect_regions
            )
            assert (
                cleaner.mask_video_with_phi_detector(source, output).frames_processed
                == 10
            )
        else:
            cleaner.build_encoder_cmd = lambda *_args, **_kwargs: ["-c:v", "libx264"]
            assert cleaner.remove_frames_from_video_streaming(
                source, [2, 5], output, 10
            )
    receipt = VideoFrameProvenancePayload.model_validate(
        VideoFrameProvenance.objects.get().payload
    )
    assert receipt.source_frame_indices == (
        [0, 1, 3, 4, 6, 7, 8, 9] if operation == "remove" else list(range(10))
    )
    assert receipt.method == (
        "opencv_decoder_input_v1"
        if operation == "detector"
        else "ffmpeg_encoder_input_v1"
    )
    if operation.startswith("mask"):
        if operation == "mask_offset":
            assert receipt.source.presentation_timestamps[0] > 0
        assert [
            Fraction(value * receipt.source.time_base_num, receipt.source.time_base_den)
            for value in receipt.source.presentation_timestamps
        ] == [
            Fraction(value * receipt.output.time_base_num, receipt.output.time_base_den)
            for value in receipt.output.presentation_timestamps
        ]
        if operation != "mask_full":
            pixels = subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-i",
                    str(output),
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ],
                check=True,
                capture_output=True,
            ).stdout
            assert sum(pixels[: 64 * 3]) < 64 * 3 * 3
            assert max(pixels[(32 * 64 + 16) * 3 : (32 * 64 + 48) * 3]) > 40


def test_canonical_cleaner_rejects_unobserved_empty_frame_removal() -> None:
    from lx_anonymizer.frame_cleaner import FrameCleaner
    from lx_anonymizer.runtime_types import VideoExecutionHooks

    from endoreg_db.services.video_storage import provenance_cleaner as adapter

    cleaner = FrameCleaner.__new__(FrameCleaner)
    cleaner.execution_hooks = VideoExecutionHooks(
        adapter._run_encoder, adapter._observe_decoded_frames
    )
    with pytest.raises(ValueError, match="at least one selected frame"):
        cleaner.remove_frames_from_video_streaming(
            Path("input.mp4"), [], Path("output.mp4")
        )


def test_canonical_mask_rejects_dimension_changing_crop() -> None:
    from lx_anonymizer.anonymization.masking import MaskApplication, MaskMode
    from lx_anonymizer.runtime_types import VideoExecutionHooks

    from endoreg_db.services.video_storage import provenance_cleaner as adapter

    masker = MaskApplication.__new__(MaskApplication)
    masker.execution_hooks = VideoExecutionHooks(
        adapter._run_encoder, adapter._observe_decoded_frames
    )
    with pytest.raises(ValueError, match="preserve image dimensions"):
        masker.mask_video_streaming(
            Path("input.mp4"), {}, Path("output.mp4"), mode=MaskMode.CROP
        )


@pytest.mark.parametrize("indices", [[0], [0, 0], [1, 0]])
def test_detector_observer_rejects_incomplete_or_reordered_frames(
    monkeypatch: pytest.MonkeyPatch, indices: list[int]
) -> None:
    from endoreg_db.services.video_storage import provenance_cleaner as adapter

    monkeypatch.setattr(adapter, "probe_video_artifact", Mock())
    monkeypatch.setattr(
        adapter, "capture_canonical_timeline", Mock(return_value=timeline("a", [0, 40]))
    )
    persist = Mock()
    monkeypatch.setattr(adapter, "persist_frame_provenance", persist)
    with (
        pytest.raises(ValueError, match="complete source frame sequence"),
        adapter._observe_decoded_frames(
            Path("input.mp4"), Path("output.mp4")
        ) as observe,
    ):
        for index in indices:
            observe(index)
    persist.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["decoder", "source_changed", "output_missing_frame"]
)
def test_detector_observer_never_persists_failed_output(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from endoreg_db.services.video_storage import provenance_cleaner as adapter

    monkeypatch.setattr(adapter, "probe_video_artifact", Mock())
    monkeypatch.setattr(
        adapter,
        "capture_canonical_timeline",
        Mock(
            side_effect=[
                timeline("a", [0, 40]),
                timeline("b", [0]),
            ]
        ),
    )
    monkeypatch.setattr(
        adapter,
        "get_file_hash",
        Mock(return_value=("b" if failure == "source_changed" else "a") * 64),
    )
    persist = Mock()
    monkeypatch.setattr(adapter, "persist_frame_provenance", persist)
    with (
        pytest.raises((ValueError, RuntimeError)),
        adapter._observe_decoded_frames(
            Path("input.mp4"), Path("output.mp4")
        ) as observe,
    ):
        observe(0)
        observe(1)
        if failure == "decoder":
            raise RuntimeError("decoder failed")
    persist.assert_not_called()


def test_cleaner_factory_passes_attempt_local_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.services.video_storage import provenance_cleaner as adapter

    constructor = Mock()
    monkeypatch.setattr(adapter, "FrameCleaner", constructor)
    first = adapter.create_frame_cleaner(quality_profile="exhaustive")
    second = adapter.create_frame_cleaner()
    assert first is constructor.return_value and second is constructor.return_value
    first_arguments = constructor.call_args_list[0].kwargs
    second_arguments = constructor.call_args_list[1].kwargs
    assert first_arguments["quality_profile"] == "exhaustive"
    assert second_arguments["quality_profile"] is None
    assert first_arguments["execution_hooks"] is not second_arguments["execution_hooks"]


def test_original_frame_timestamps_cannot_fall_back_to_estimates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from endoreg_db.services.video_storage import probes

    monkeypatch.setattr(ffmpeg_wrapper, "resolve_ffprobe_executable", lambda: "ffprobe")

    def fake_probe(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "frames": [
                        {
                            "best_effort_timestamp": 0,
                            "best_effort_timestamp_time": "0.0",
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(probes.subprocess, "run", fake_probe)
    with pytest.raises(probes.VideoStorageNormalizationError, match="original"):
        probes.probe_video_frame_timestamps(
            Path("video.mp4"), require_original_pts=True
        )
