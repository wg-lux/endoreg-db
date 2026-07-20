from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from endoreg_db.models import Center, Frame, VideoFile
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services import video_storage_normalization as normalization
from endoreg_db.services.video_storage import contracts as storage_contracts


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

    def fake_get_stream_info(_path: Path) -> dict[str, object]:
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
                        {"best_effort_timestamp_time": "0.000"},
                        {"best_effort_timestamp_time": "0.040"},
                        {"best_effort_timestamp_time": "0.040"},
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

    normalization.persist_video_source_timeline(video, tmp_path / "source.mp4")

    timestamps = list(
        Frame.objects.filter(video=video)
        .order_by("frame_number")
        .values_list("timestamp", flat=True)
    )
    video.refresh_from_db()
    assert timestamps == [0.0, 0.033, 0.091]
    meta = video.meta
    assert isinstance(meta, dict)
    evidence = VideoSourceTimelineEvidence.model_validate(meta["source_timeline"])
    assert evidence.timeline_version == "pts_v1"
    assert evidence.timestamp_mapping == "ffprobe_pts"


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
