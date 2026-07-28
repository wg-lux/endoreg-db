from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from endoreg_db.models import Center, Frame, VideoFile
from endoreg_db.schemas.video_storage import (
    FramePresentationTimestamp,
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services import video_storage_normalization as normalization
from endoreg_db.services.video_storage import contracts as storage_contracts
from endoreg_db.utils.video.command_construction import FFprobeInputPolicy


def _timeline(
    *,
    fps_num: int = 25,
    fps_den: int = 1,
    duration_seconds: float = 10.0,
    frame_count: int = 250,
    variable_frame_rate: bool = False,
    time_base_num: int | None = None,
    time_base_den: int | None = None,
) -> VideoTimelineContract:
    return VideoTimelineContract(
        fps_num=fps_num,
        fps_den=fps_den,
        duration_seconds=duration_seconds,
        frame_count=frame_count,
        variable_frame_rate=variable_frame_rate,
        time_base_num=time_base_num,
        time_base_den=time_base_den,
    )


def _probe(
    *,
    timeline: VideoTimelineContract | None = None,
    size_bytes: int = 1_000_000,
    bit_rate_bps: int = 800_000,
    width: int = 1920,
    height: int = 1080,
) -> VideoArtifactProbe:
    return VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=width,
        height=height,
        bit_rate_bps=bit_rate_bps,
        size_bytes=size_bytes,
        timeline=timeline or _timeline(),
    )


def _ffprobe_payload(
    *,
    codec_type: str = "video",
    codec_name: str | None = "h264",
    pixel_format: str | None = "yuv420p",
    width: int | None = 1920,
    height: int | None = 1080,
    duration: str | None = "10.0",
    average_frame_rate: str | None = "25/1",
    nominal_frame_rate: str | None = "25/1",
    time_base: str | None = "1/90000",
    frame_count: str | None = "250",
    bit_rate: str | None = "8000000",
    format_duration: str | None = None,
    format_bit_rate: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "streams": [
            {
                "codec_type": codec_type,
                "codec_name": codec_name,
                "pix_fmt": pixel_format,
                "width": width,
                "height": height,
                "duration": duration,
                "avg_frame_rate": average_frame_rate,
                "r_frame_rate": nominal_frame_rate,
                "time_base": time_base,
                "nb_frames": frame_count,
                "bit_rate": bit_rate,
            }
        ]
    }
    if format_duration is not None or format_bit_rate is not None:
        payload["format"] = {
            "duration": format_duration,
            "bit_rate": format_bit_rate,
        }
    return payload


def _patch_ffprobe_payload(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    def fake_get_stream_info(
        _path: Path,
        *,
        input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
    ) -> dict[str, object]:
        del input_policy
        return payload

    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "get_stream_info",
        fake_get_stream_info,
    )


def _patch_frame_probe_stdout(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "resolve_ffprobe_executable",
        lambda: "/usr/bin/ffprobe",
    )

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        _ = args, kwargs
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(normalization.subprocess, "run", fake_run)


@pytest.mark.unit
def test_storage_profile_caps_bitrate_without_changing_fps() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    args = profile.ffmpeg_output_args()

    assert args[args.index("-maxrate") + 1] == "12000000"
    assert args[args.index("-bufsize") + 1] == "24000000"
    assert args[args.index("-fps_mode") + 1] == "passthrough"
    assert "-fpsmax" not in args


@pytest.mark.unit
def test_annotation_fps_profile_downsamples_without_spatial_resampling() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    args = profile.ffmpeg_output_args(target_fps=50.0)
    filter_chain = args[args.index("-vf") + 1]

    assert (
        filter_chain == "scale=iw:ih:in_range=auto:out_range=full,format=yuv420p,fps=50"
    )
    assert args[args.index("-fps_mode") + 1] == "cfr"


@pytest.mark.unit
def test_storage_profile_rejects_output_above_resolution_or_fps_caps() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
        max_width=1920,
        max_height=1080,
        max_source_fps=60.0,
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="Output dimensions exceed profile",
    ):
        normalization.assert_storage_compliance(
            _probe(width=3840, height=2160),
            profile=profile,
        )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="Output FPS exceeds profile",
    ):
        normalization.assert_storage_compliance(
            _probe(timeline=_timeline(fps_num=61, frame_count=610)),
            profile=profile,
        )


@pytest.mark.unit
def test_storage_capacity_uses_projected_output_for_hard_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_disk_usage(_path: Path) -> SimpleNamespace:
        return SimpleNamespace(total=10_000, used=4_000, free=6_000)

    monkeypatch.setattr(
        storage_contracts.shutil,
        "disk_usage",
        fake_disk_usage,
    )
    monkeypatch.setattr(
        storage_contracts,
        "get_video_storage_warning_free_bytes",
        lambda: 5_000,
    )
    monkeypatch.setattr(
        storage_contracts,
        "get_video_storage_stop_free_bytes",
        lambda: 2_000,
    )

    report = normalization.video_storage_capacity(
        storage_root=tmp_path,
        projected_temporary_bytes=4_500,
    )

    assert report.status == "stop"
    assert report.as_dict()["projected_free_bytes"] == 1_500


@pytest.mark.unit
def test_probe_video_artifact_keeps_rational_fps_time_base_and_vfr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"video")

    def fake_get_stream_info(
        _path: Path,
        *,
        input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
    ) -> dict[str, object]:
        del input_policy
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "pix_fmt": "yuv420p",
                    "width": 1920,
                    "height": 1080,
                    "duration": "10.0",
                    "avg_frame_rate": "30000/1001",
                    "r_frame_rate": "60/1",
                    "time_base": "1/90000",
                    "nb_frames": "300",
                    "bit_rate": "8000000",
                }
            ]
        }

    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "get_stream_info",
        fake_get_stream_info,
    )

    probe = normalization.probe_video_artifact(path)

    assert probe.timeline.fps_num == 30000
    assert probe.timeline.fps_den == 1001
    assert probe.timeline.time_base_num == 1
    assert probe.timeline.time_base_den == 90000
    assert probe.timeline.variable_frame_rate is True
    assert probe.timeline.frame_count == 300


@pytest.mark.unit
@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (None, "Video artifact is missing"),
        (b"", "Video artifact is empty"),
    ],
)
def test_probe_video_artifact_rejects_missing_or_empty_artifact(
    tmp_path: Path,
    contents: bytes | None,
    message: str,
) -> None:
    path = tmp_path / "source.mp4"
    if contents is not None:
        path.write_bytes(contents)

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match=message,
    ):
        normalization.probe_video_artifact(path)


@pytest.mark.unit
def test_probe_video_artifact_preserves_metadata_fallback_order_and_input_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"video")
    observed_policies: list[FFprobeInputPolicy] = []

    def fake_get_stream_info(
        _path: Path,
        *,
        input_policy: FFprobeInputPolicy = FFprobeInputPolicy.DEFAULT,
    ) -> dict[str, object]:
        observed_policies.append(input_policy)
        return _ffprobe_payload(
            duration=None,
            average_frame_rate=None,
            nominal_frame_rate="25/1",
            time_base=None,
            frame_count="N/A",
            bit_rate=None,
            format_duration="2.0",
            format_bit_rate="700000",
        )

    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "get_stream_info",
        fake_get_stream_info,
    )

    probe = normalization.probe_video_artifact(
        path,
        input_policy=FFprobeInputPolicy.TRUSTED_LOCAL_HLS,
    )

    assert observed_policies == [FFprobeInputPolicy.TRUSTED_LOCAL_HLS]
    assert probe.timeline.fps_num == 25
    assert probe.timeline.fps_den == 1
    assert probe.timeline.variable_frame_rate is False
    assert probe.timeline.duration_seconds == 2.0
    assert probe.timeline.frame_count == 50
    assert probe.timeline.time_base_num is None
    assert probe.timeline.time_base_den is None
    assert probe.bit_rate_bps == 700000


@pytest.mark.unit
def test_probe_video_artifact_uses_average_rate_within_vfr_tolerance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"video")
    _patch_ffprobe_payload(
        monkeypatch,
        _ffprobe_payload(
            average_frame_rate="30000/1001",
            nominal_frame_rate="2997/100",
        ),
    )

    probe = normalization.probe_video_artifact(path)

    assert probe.timeline.fps_num == 30000
    assert probe.timeline.fps_den == 1001
    assert probe.timeline.variable_frame_rate is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"streams": []}, "ffprobe found no video stream"),
        (_ffprobe_payload(width=None), "ffprobe did not report video dimensions"),
        (
            _ffprobe_payload(codec_name=None),
            "ffprobe did not report codec and pixel format",
        ),
        (
            _ffprobe_payload(
                average_frame_rate="0/0",
                nominal_frame_rate="0/0",
            ),
            "ffprobe did not report a positive frame rate",
        ),
        (
            _ffprobe_payload(duration=None),
            "ffprobe did not report a positive duration",
        ),
    ],
)
def test_probe_video_artifact_rejects_incomplete_probe_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"video")
    _patch_ffprobe_payload(monkeypatch, payload)

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match=message,
    ):
        normalization.probe_video_artifact(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _ffprobe_payload(average_frame_rate="invalid"),
            "Invalid avg_frame_rate ratio from ffprobe",
        ),
        (
            _ffprobe_payload(duration="invalid"),
            "Invalid duration value from ffprobe",
        ),
        (
            _ffprobe_payload(frame_count="invalid"),
            "Invalid nb_frames value from ffprobe",
        ),
        (
            _ffprobe_payload(bit_rate="invalid"),
            "Invalid bit_rate value from ffprobe",
        ),
    ],
)
def test_probe_video_artifact_rejects_malformed_numeric_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"video")
    _patch_ffprobe_payload(monkeypatch, payload)

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match=message,
    ):
        normalization.probe_video_artifact(path)


@pytest.mark.unit
def test_temporal_gate_rejects_changed_frame_identity() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="frame count drifted",
    ):
        normalization.assert_temporal_equivalence(
            _timeline(),
            _timeline(frame_count=249),
            profile=profile,
        )


@pytest.mark.unit
def test_storage_normalization_rejects_spatial_upsampling() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="must preserve source dimensions",
    ):
        normalization.validate_normalized_output(
            source=_probe(width=1280, height=720),
            output=_probe(width=1920, height=1080),
            profile=profile,
        )


@pytest.mark.unit
def test_annotation_fps_resample_accepts_downsampling_before_annotation() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )
    source = _probe(
        timeline=_timeline(fps_num=60, frame_count=600),
    )
    output = _probe(
        timeline=_timeline(fps_num=50, frame_count=500),
    )

    evidence = normalization.validate_annotation_fps_resample(
        source=source,
        output=output,
        max_fps=50.0,
        profile=profile,
    )

    assert evidence.profile_name == "annotation_fps_resample_v1"
    assert evidence.max_fps == 50.0
    assert evidence.timeline_version == "pts_v1"
    assert evidence.source.timeline.frame_count == 600
    assert evidence.output.timeline.frame_count == 500


@pytest.mark.unit
def test_annotation_fps_resample_rejects_fps_upsampling() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="only valid above the target FPS",
    ):
        normalization.validate_annotation_fps_resample(
            source=_probe(timeline=_timeline(fps_num=25)),
            output=_probe(timeline=_timeline(fps_num=50, frame_count=500)),
            max_fps=50.0,
            profile=profile,
        )


@pytest.mark.unit
def test_annotation_fps_resample_rejects_resolution_upsampling() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="must preserve source dimensions",
    ):
        normalization.validate_annotation_fps_resample(
            source=_probe(
                timeline=_timeline(fps_num=60, frame_count=600),
                width=1280,
                height=720,
            ),
            output=_probe(
                timeline=_timeline(fps_num=50, frame_count=500),
                width=1920,
                height=1080,
            ),
            max_fps=50.0,
            profile=profile,
        )


@pytest.mark.unit
def test_probe_video_frame_pts_rejects_non_monotonic_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "resolve_ffprobe_executable",
        lambda: "/usr/bin/ffprobe",
    )

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "frames": [
                        {
                            "best_effort_timestamp": "0",
                            "best_effort_timestamp_time": "0.000",
                        },
                        {
                            "best_effort_timestamp": "3600",
                            "best_effort_timestamp_time": "0.040",
                        },
                        {
                            "best_effort_timestamp": "7200",
                            "best_effort_timestamp_time": "0.040",
                        },
                    ]
                }
            )
        )

    monkeypatch.setattr(normalization.subprocess, "run", fake_run)

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="strictly increasing",
    ):
        normalization.probe_video_frame_pts(Path("video.mp4"))


@pytest.mark.unit
def test_probe_video_frame_timestamps_accepts_numeric_ffprobe_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "resolve_ffprobe_executable",
        lambda: "/usr/bin/ffprobe",
    )

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        _ = args, kwargs
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "frames": [
                        {
                            "best_effort_timestamp": 0,
                            "best_effort_timestamp_time": "0.000",
                        },
                        {
                            "best_effort_timestamp": 3600,
                            "best_effort_timestamp_time": "0.040",
                        },
                    ]
                }
            )
        )

    monkeypatch.setattr(normalization.subprocess, "run", fake_run)

    timestamps = normalization.probe_video_frame_timestamps(Path("video.mp4"))

    assert [row.presentation_timestamp for row in timestamps] == [0, 3600]
    assert [row.presentation_time_seconds for row in timestamps] == [0.0, 0.04]


@pytest.mark.unit
def test_probe_video_frame_pts_preserves_irregular_ffprobe_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_frame_probe_stdout(
        monkeypatch,
        json.dumps(
            {
                "frames": [
                    {
                        "best_effort_timestamp": "125",
                        "best_effort_timestamp_time": "0.001389",
                    },
                    {
                        "best_effort_timestamp": "2970",
                        "best_effort_timestamp_time": "0.033",
                    },
                    {
                        "best_effort_timestamp": "8190",
                        "best_effort_timestamp_time": "0.091",
                    },
                ]
            }
        ),
    )

    assert normalization.probe_video_frame_pts(Path("video.mp4")) == [
        0.001389,
        0.033,
        0.091,
    ]


@pytest.mark.unit
def test_probe_video_frame_timestamps_preserves_ffprobe_command_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "resolve_ffprobe_executable",
        lambda: "/usr/bin/ffprobe",
    )
    observed_commands: list[list[str]] = []
    observed_options: list[tuple[bool, bool, bool, int]] = []

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> SimpleNamespace:
        observed_commands.append(command)
        observed_options.append((check, capture_output, text, timeout))
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "frames": [
                        {
                            "best_effort_timestamp": "0",
                            "best_effort_timestamp_time": "0.000",
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr(normalization.subprocess, "run", fake_run)

    normalization.probe_video_frame_timestamps(Path("video.mp4"))

    assert observed_commands == [
        [
            "/usr/bin/ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp,best_effort_timestamp_time",
            "-of",
            "json",
            "video.mp4",
        ]
    ]
    assert observed_options == [(True, True, True, 3600)]


@pytest.mark.unit
def test_probe_video_frame_timestamps_requires_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "resolve_ffprobe_executable",
        lambda: None,
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="ffprobe executable is not available",
    ):
        normalization.probe_video_frame_timestamps(Path("video.mp4"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [
        OSError("ffprobe unavailable"),
        subprocess.CalledProcessError(1, ["/usr/bin/ffprobe"]),
    ],
)
def test_probe_video_frame_timestamps_preserves_subprocess_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: OSError | subprocess.SubprocessError,
) -> None:
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "resolve_ffprobe_executable",
        lambda: "/usr/bin/ffprobe",
    )

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        _ = args, kwargs
        raise error

    monkeypatch.setattr(normalization.subprocess, "run", fake_run)

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="Could not probe frame PTS for video.mp4",
    ) as exc_info:
        normalization.probe_video_frame_timestamps(Path("video.mp4"))

    assert exc_info.value.__cause__ is error


@pytest.mark.unit
@pytest.mark.parametrize(
    "stdout",
    [
        "not-json",
        json.dumps({}),
    ],
)
def test_probe_video_frame_timestamps_rejects_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    _patch_frame_probe_stdout(monkeypatch, stdout)

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="ffprobe returned invalid frame PTS for video.mp4",
    ):
        normalization.probe_video_frame_timestamps(Path("video.mp4"))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            {
                "best_effort_timestamp": "0",
                "best_effort_timestamp_time": None,
            },
            "Frame 0 has no presentation timestamp",
        ),
        (
            {
                "best_effort_timestamp": None,
                "best_effort_timestamp_time": "0.000",
            },
            "Frame 0 has no presentation timestamp",
        ),
        (
            {
                "best_effort_timestamp": "0",
                "best_effort_timestamp_time": "invalid",
            },
            "Frame 0 has an invalid presentation timestamp",
        ),
        (
            {
                "best_effort_timestamp": "invalid",
                "best_effort_timestamp_time": "0.000",
            },
            "Frame 0 has an invalid presentation timestamp",
        ),
        (
            {
                "best_effort_timestamp": "0",
                "best_effort_timestamp_time": "nan",
            },
            "Frame 0 has an invalid presentation timestamp",
        ),
        (
            {
                "best_effort_timestamp": "0",
                "best_effort_timestamp_time": "-0.001",
            },
            "Frame 0 has an invalid presentation timestamp",
        ),
        (
            {
                "best_effort_timestamp": "-1",
                "best_effort_timestamp_time": "0.000",
            },
            "Frame 0 has an invalid presentation timestamp",
        ),
    ],
)
def test_probe_video_frame_timestamps_rejects_invalid_rows(
    monkeypatch: pytest.MonkeyPatch,
    row: dict[str, object],
    message: str,
) -> None:
    _patch_frame_probe_stdout(
        monkeypatch,
        json.dumps({"frames": [row]}),
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match=message,
    ):
        normalization.probe_video_frame_timestamps(Path("video.mp4"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "second_row",
    [
        {
            "best_effort_timestamp": "3600",
            "best_effort_timestamp_time": "0.000",
        },
        {
            "best_effort_timestamp": "0",
            "best_effort_timestamp_time": "0.040",
        },
    ],
)
def test_probe_video_frame_timestamps_requires_both_coordinates_to_increase(
    monkeypatch: pytest.MonkeyPatch,
    second_row: dict[str, object],
) -> None:
    _patch_frame_probe_stdout(
        monkeypatch,
        json.dumps(
            {
                "frames": [
                    {
                        "best_effort_timestamp": "0",
                        "best_effort_timestamp_time": "0.000",
                    },
                    second_row,
                ]
            }
        ),
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="Frame presentation timestamps must be strictly increasing",
    ):
        normalization.probe_video_frame_timestamps(Path("video.mp4"))


@pytest.mark.unit
def test_probe_video_frame_timestamps_rejects_empty_frame_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_frame_probe_stdout(monkeypatch, json.dumps({"frames": []}))

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="ffprobe returned no frame timestamps",
    ):
        normalization.probe_video_frame_timestamps(Path("video.mp4"))


@pytest.mark.django_db
def test_persist_video_source_timeline_uses_probed_pts_for_vfr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    center = Center.objects.create(name="pts-center", display_name="PTS Center")
    video = VideoFile.objects.create(
        center=center,
        video_hash="pts-video",
        fps=25.0,
        duration=0.2,
        frame_count=3,
    )
    Frame.objects.bulk_create(
        [
            Frame(video=video, frame_number=0, relative_path="0.jpg"),
            Frame(video=video, frame_number=1, relative_path="1.jpg"),
            Frame(video=video, frame_number=2, relative_path="2.jpg"),
        ]
    )
    source_probe = _probe(
        timeline=_timeline(
            duration_seconds=0.2,
            frame_count=3,
            variable_frame_rate=True,
            time_base_num=1,
            time_base_den=90_000,
        )
    )

    def fake_probe_video_artifact(_path: Path) -> VideoArtifactProbe:
        return source_probe

    def fake_probe_video_frame_pts(_path: Path) -> list[float]:
        return [0.0, 0.033, 0.091]

    def fake_probe_video_frame_timestamps(
        _path: Path,
    ) -> list[FramePresentationTimestamp]:
        return [
            FramePresentationTimestamp(
                presentation_timestamp=0,
                presentation_time_seconds=0.0,
            ),
            FramePresentationTimestamp(
                presentation_timestamp=2970,
                presentation_time_seconds=0.033,
            ),
            FramePresentationTimestamp(
                presentation_timestamp=8190,
                presentation_time_seconds=0.091,
            ),
        ]

    monkeypatch.setattr(
        normalization,
        "probe_video_artifact",
        fake_probe_video_artifact,
    )
    monkeypatch.setattr(
        normalization,
        "probe_video_frame_pts",
        fake_probe_video_frame_pts,
    )
    monkeypatch.setattr(
        normalization,
        "probe_video_frame_timestamps",
        fake_probe_video_frame_timestamps,
    )

    normalization.persist_video_source_timeline(video, tmp_path / "source.mp4")

    timestamps = list(
        Frame.objects.filter(video=video)
        .order_by("frame_number")
        .values_list("timestamp", flat=True)
    )
    video.refresh_from_db()
    assert timestamps == [0.0, 0.033, 0.091]
    assert list(
        Frame.objects.filter(video=video)
        .order_by("frame_number")
        .values_list("presentation_timestamp", flat=True)
    ) == [0, 2970, 8190]
    meta = video.meta
    assert isinstance(meta, dict)
    evidence = VideoSourceTimelineEvidence.model_validate(meta["source_timeline"])
    assert evidence.timeline_version == "pts_v1"
    assert evidence.timestamp_mapping == "ffprobe_pts"

    def fake_inconsistent_frame_timestamps(
        _path: Path,
    ) -> list[FramePresentationTimestamp]:
        return [
            FramePresentationTimestamp(
                presentation_timestamp=0,
                presentation_time_seconds=0.0,
            ),
            FramePresentationTimestamp(
                presentation_timestamp=2970,
                presentation_time_seconds=0.05,
            ),
            FramePresentationTimestamp(
                presentation_timestamp=8190,
                presentation_time_seconds=0.091,
            ),
        ]

    monkeypatch.setattr(
        normalization,
        "probe_video_frame_timestamps",
        fake_inconsistent_frame_timestamps,
    )
    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="does not match stream time base",
    ):
        normalization.persist_video_source_timeline(
            video,
            tmp_path / "source.mp4",
        )


@pytest.mark.unit
def test_storage_gate_rejects_output_over_duration_budget() -> None:
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=100_000,
        fixed_overhead_bytes=0,
    )

    with pytest.raises(
        normalization.VideoStorageNormalizationError,
        match="Output size exceeds profile",
    ):
        normalization.assert_storage_compliance(
            _probe(size_bytes=1_000_001),
            profile=profile,
        )


@pytest.mark.unit
def test_normalize_video_file_replaces_only_after_all_gates_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reference_path = tmp_path / "raw.mp4"
    input_path = tmp_path / "anonymized.mp4"
    reference_path.write_bytes(b"raw")
    input_path.write_bytes(b"unbounded")
    reference_probe = _probe(size_bytes=2_000_000)
    unbounded_probe = _probe(size_bytes=20_000_000, bit_rate_bps=20_000_000)
    bounded_probe = _probe(size_bytes=1_000_000, bit_rate_bps=800_000)

    def fake_probe(path: Path) -> VideoArtifactProbe:
        if path == reference_path:
            return reference_probe
        if path == input_path:
            return unbounded_probe
        return bounded_probe

    def fake_transcode(
        *,
        input_path: Path,
        output_path: Path,
        **kwargs: object,
    ) -> Path:
        assert kwargs["extra_args"]
        output_path.write_bytes(b"bounded")
        return output_path

    monkeypatch.setattr(normalization, "probe_video_artifact", fake_probe)
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "transcode_video",
        fake_transcode,
    )
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    evidence = normalization.normalize_video_file(
        input_path=input_path,
        reference_path=reference_path,
        quality_mode="quality",
        profile=profile,
    )

    assert input_path.read_bytes() == b"bounded"
    assert evidence.temporal_equivalent is True
    assert evidence.storage_compliant is True
    assert evidence.profile_name == "test"


@pytest.mark.unit
def test_ensure_video_file_profile_copies_compliant_input_without_transcoding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "incoming.mp4"
    output_path = tmp_path / "canonical.mp4"
    input_path.write_bytes(b"already-compliant")
    compliant_probe = _probe()
    probed_paths: list[Path] = []

    def fake_probe(path: Path) -> VideoArtifactProbe:
        probed_paths.append(path)
        return compliant_probe

    def fail_transcode(**_kwargs: object) -> None:
        raise AssertionError("Compliant input must not be transcoded")

    monkeypatch.setattr(normalization, "probe_video_artifact", fake_probe)
    monkeypatch.setattr(
        normalization.ffmpeg_wrapper,
        "transcode_video",
        fail_transcode,
    )
    profile = normalization.VideoStorageProfile(
        name="test",
        max_bit_rate_bps=12_000_000,
        max_bytes_per_second=1_600_000,
        fixed_overhead_bytes=1024,
    )

    evidence = normalization.ensure_video_file_profile(
        input_path=input_path,
        output_path=output_path,
        reference_path=input_path,
        quality_mode="quality",
        profile=profile,
    )

    assert input_path.read_bytes() == b"already-compliant"
    assert output_path.read_bytes() == b"already-compliant"
    assert evidence.storage_compliant is True
    assert probed_paths[0:2] == [input_path, input_path]
    assert probed_paths[-1] != output_path
