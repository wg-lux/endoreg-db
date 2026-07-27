# pyright: reportPrivateUsage=false
import os
from collections.abc import Callable
from contextlib import nullcontext
from fractions import Fraction
from pathlib import Path
from typing import NoReturn, Protocol, cast

import pytest
from lx_dtypes.models import SensitiveMeta
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.video_processing import video_anonymization
from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    Frame,
    FrameBoxAnnotation,
    VideoFile,
)

RealVideoAnonymizer = video_anonymization.VideoAnonymizer


class _FrameBoxAnnotationLike(Protocol):
    frame: Frame
    label: "_NamedEntityLike"
    information_source: "_NamedEntityLike"
    external_annotation_id: str
    float_value: float
    x: int
    y: int
    width: int
    height: int
    image_width: int
    image_height: int
    value: bool
    annotator: str


class _NamedEntityLike(Protocol):
    name: str


def _valid_stream_info(*, width: int = 640, height: int = 480) -> JsonObject:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": width,
                "height": height,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30000/1001",
            }
        ]
    }


def _create_processor_with_roi(
    name: str,
    center: Center | None = None,
) -> EndoscopyProcessor:
    processor = EndoscopyProcessor.objects.create(
        name=name,
        image_width=1920,
        image_height=1080,
        endoscope_image_x=550,
        endoscope_image_y=0,
        endoscope_image_width=1350,
        endoscope_image_height=1080,
        examination_date_x=100,
        examination_date_y=10,
        examination_date_width=200,
        examination_date_height=40,
        patient_first_name_x=100,
        patient_first_name_y=60,
        patient_first_name_width=200,
        patient_first_name_height=40,
        patient_last_name_x=320,
        patient_last_name_y=60,
        patient_last_name_width=240,
        patient_last_name_height=40,
        patient_dob_x=100,
        patient_dob_y=110,
        patient_dob_width=160,
        patient_dob_height=40,
    )
    if center is not None:
        processor.centers.add(center)
    return processor


def _create_source_file(tmp_path: Path, name: str = "source.mp4") -> Path:
    source_path = tmp_path / name
    source_path.write_bytes(b"source-video")
    return source_path


def _create_import_context(
    *,
    file_path: Path,
    center: Center,
    video: VideoFile,
    processor_name: str,
) -> ImportContext:
    return ImportContext(
        file_path=file_path,
        center_name=center.name,
        current_video=video,
        processor_name=processor_name,
    )


def _unchecked_import_context(
    *,
    file_path: Path,
    center: Center,
    video: VideoFile,
    processor_name: str,
) -> ImportContext:
    return ImportContext.model_construct(
        file_path=file_path,
        center_name=center.name,
        current_video=video,
        processor_name=processor_name,
    )


def _resolve_missing_ffmpeg() -> None:
    return None


def _resolve_missing_ffprobe() -> str:
    return "/smart/bin/ffprobe"


def _ensure_ffmpeg_tools_noop() -> None:
    return None


def _stream_info_for_path(path: Path) -> JsonObject:
    return _valid_stream_info()


def _small_stream_info_for_path(path: Path) -> JsonObject:
    return _valid_stream_info(width=640, height=480)


@pytest.mark.unit
def test_source_frame_rate_preserves_rational_rate_and_uses_nominal_when_needed() -> (
    None
):
    stream = video_anonymization._first_video_stream(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "avg_frame_rate": "0/0",
                    "r_frame_rate": "30000/1001",
                }
            ]
        }
    )

    assert stream is not None
    assert video_anonymization._source_frame_rate(stream) == Fraction(30000, 1001)


@pytest.mark.unit
def test_source_frame_rate_rejects_missing_rate() -> None:
    stream = video_anonymization._first_video_stream(
        {"streams": [{"codec_type": "video"}]}
    )

    assert stream is not None
    with pytest.raises(RuntimeError, match="no positive rational"):
        video_anonymization._source_frame_rate(stream)


def _path_string_resolver(path: Path) -> Callable[[], str]:
    def resolve_path() -> str:
        return str(path)

    return resolve_path


def _processed_video_dir_for(output_dir: Path) -> Callable[[], Path]:
    def _processed_video_dir() -> Path:
        return output_dir

    return _processed_video_dir


def _quarantine_dir_for(quarantine_dir: Path) -> Callable[[], Path]:
    def _quarantine_dir() -> Path:
        return quarantine_dir

    return _quarantine_dir


def _sensitive_meta_storage_noop(
    sensitive_meta: SensitiveMeta,
    current_video: VideoFile,
) -> None:
    return None


def _local_raw_file_context(path: Path) -> Callable[[], nullcontext[Path]]:
    def ensure_local_raw_file() -> nullcontext[Path]:
        return nullcontext(path)

    return ensure_local_raw_file


def _raise_local_raw_file() -> NoReturn:
    raise AssertionError("local_source_path should avoid rematerializing raw video")


@pytest.mark.unit
def test_ensure_ffmpeg_tools_on_path_prepends_resolved_tool_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool_dir = tmp_path / "ffmpeg-bin"
    tool_dir.mkdir()
    ffmpeg = tool_dir / "ffmpeg"
    ffprobe = tool_dir / "ffprobe"
    ffmpeg.write_text("#!/bin/sh\n", encoding="utf-8")
    ffprobe.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffmpeg_executable",
        _path_string_resolver(ffmpeg),
    )
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffprobe_executable",
        _path_string_resolver(ffprobe),
    )

    video_anonymization._ensure_ffmpeg_tools_on_path()

    path_parts = os.environ["PATH"].split(os.pathsep)
    assert path_parts[0] == tool_dir.as_posix()
    assert "/usr/bin" in path_parts


@pytest.mark.unit
def test_ensure_ffmpeg_tools_on_path_errors_when_tools_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffmpeg_executable",
        _resolve_missing_ffmpeg,
    )
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffprobe_executable",
        _resolve_missing_ffprobe,
    )

    with pytest.raises(RuntimeError, match="FFmpeg and ffprobe are required"):
        video_anonymization._ensure_ffmpeg_tools_on_path()


@pytest.mark.django_db
def test_get_processor_roi_info_errors_without_processor_name(tmp_path: Path) -> None:
    center = Center.objects.create(
        name="roi-none-center",
        display_name="ROI None Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-none-hash")
    ctx = _unchecked_import_context(
        file_path=_create_source_file(tmp_path),
        center=center,
        video=video,
        processor_name="",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    with pytest.raises(RuntimeError, match="requires a processor_name"):
        anonymizer._get_processor_roi_info(ctx)


@pytest.mark.django_db
def test_get_processor_roi_info_errors_for_unknown_processor(tmp_path: Path) -> None:
    center = Center.objects.create(
        name="roi-unknown-center",
        display_name="ROI Unknown Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-unknown-hash")
    ctx = _create_import_context(
        file_path=_create_source_file(tmp_path),
        center=center,
        video=video,
        processor_name="unknown_processor",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    with pytest.raises(RuntimeError, match="unknown_processor"):
        anonymizer._get_processor_roi_info(ctx)


@pytest.mark.django_db
def test_get_processor_roi_info_errors_for_invalid_endoscope_roi(
    tmp_path: Path,
) -> None:
    center = Center.objects.create(
        name="roi-invalid-center",
        display_name="ROI Invalid Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-invalid-hash")
    processor = EndoscopyProcessor.objects.create(name="roi_invalid_processor")
    processor.centers.add(center)
    ctx = _create_import_context(
        file_path=_create_source_file(tmp_path),
        center=center,
        video=video,
        processor_name="roi_invalid_processor",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    with pytest.raises(RuntimeError, match="invalid endoscope image ROI"):
        anonymizer._get_processor_roi_info(ctx)


@pytest.mark.django_db
def test_get_processor_roi_info_returns_canonical_mask_roi_with_source_dimensions(
    tmp_path: Path,
) -> None:
    center = Center.objects.create(
        name="roi-dimensions-center",
        display_name="ROI Dimensions Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="roi-dimensions-hash")
    processor = EndoscopyProcessor.objects.create(
        name="roi_dimensions_processor",
        image_width=1920,
        image_height=1080,
        endoscope_image_x=550,
        endoscope_image_y=0,
        endoscope_image_width=1350,
        endoscope_image_height=1080,
        examination_date_x=100,
        examination_date_y=10,
        examination_date_width=200,
        examination_date_height=40,
        patient_first_name_x=100,
        patient_first_name_y=60,
        patient_first_name_width=200,
        patient_first_name_height=40,
        patient_last_name_x=320,
        patient_last_name_y=60,
        patient_last_name_width=240,
        patient_last_name_height=40,
        patient_dob_x=100,
        patient_dob_y=110,
        patient_dob_width=160,
        patient_dob_height=40,
    )
    processor.centers.add(center)
    ctx = _create_import_context(
        file_path=_create_source_file(tmp_path),
        center=center,
        video=video,
        processor_name="roi_dimensions_processor",
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    endoscope_roi, sensitive_rois = anonymizer._get_processor_roi_info(ctx)

    assert endoscope_roi == {
        "x": 550,
        "y": 0,
        "width": 1350,
        "height": 1080,
        "image_width": 1920,
        "image_height": 1080,
    }
    assert "endoscope_image_x" not in endoscope_roi
    assert sensitive_rois["examination_date"] == {
        "x": 100,
        "y": 10,
        "width": 200,
        "height": 40,
    }
    assert "examination_time" not in sensitive_rois


def _phi_observation(frame_number: int = 5) -> JsonObject:
    return {
        "frame_number": frame_number,
        "image_width": 1920,
        "image_height": 1080,
        "source_tags": ["phi_detector"],
        "phi_regions": [
            {
                "source": "phi_detector",
                "x": 100,
                "y": 50,
                "width": 320,
                "height": 80,
                "confidence": 0.71,
            }
        ],
    }


@pytest.mark.django_db
def test_persist_phi_region_proposals_creates_frame_box_annotation() -> None:
    center = Center.objects.create(
        name="phi-proposal-center",
        display_name="PHI Proposal Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-hash")
    frame = Frame.objects.create(
        video=video,
        frame_number=5,
        relative_path="frame_5.jpg",
        is_extracted=True,
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    count = anonymizer._persist_phi_region_proposals(
        video,
        {"frame_observations": [_phi_observation()]},
    )

    assert count == 1
    annotation = cast(_FrameBoxAnnotationLike, FrameBoxAnnotation.objects.get())
    assert annotation.frame == frame
    assert annotation.label is not None
    assert annotation.information_source is not None
    assert annotation.external_annotation_id is not None
    assert annotation.label.name == "phi_region"
    assert annotation.information_source.name == "lx_anonymizer_phi_detector"
    assert annotation.annotator == "system:lx_anonymizer"
    assert annotation.value is True
    assert annotation.float_value == 0.71
    assert annotation.x == 100
    assert annotation.y == 50
    assert annotation.width == 320
    assert annotation.height == 80
    assert annotation.image_width == 1920
    assert annotation.image_height == 1080
    assert annotation.external_annotation_id.startswith(
        "phi-video-hash:5:phi_detector:"
    )


@pytest.mark.django_db
def test_persist_phi_region_proposals_skips_when_frame_row_is_missing() -> None:
    center = Center.objects.create(
        name="phi-proposal-missing-frame-center",
        display_name="PHI Proposal Missing Frame Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-no-frame")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    count = anonymizer._persist_phi_region_proposals(
        video,
        {"frame_observations": [_phi_observation()]},
    )

    assert count == 0
    assert FrameBoxAnnotation.objects.count() == 0


@pytest.mark.django_db
def test_persist_phi_region_proposals_is_idempotent_by_external_annotation_id() -> None:
    center = Center.objects.create(
        name="phi-proposal-idempotent-center",
        display_name="PHI Proposal Idempotent Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-idempotent")
    Frame.objects.create(
        video=video,
        frame_number=5,
        relative_path="frame_5.jpg",
        is_extracted=True,
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    metadata: JsonObject = {"frame_observations": [_phi_observation()]}

    first_count = anonymizer._persist_phi_region_proposals(video, metadata)
    second_count = anonymizer._persist_phi_region_proposals(video, metadata)

    assert first_count == 1
    assert second_count == 1
    assert FrameBoxAnnotation.objects.count() == 1


@pytest.mark.django_db
def test_persist_phi_region_proposals_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    center = Center.objects.create(
        name="phi-proposal-failure-center",
        display_name="PHI Proposal Failure Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-failure")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    def fail(video: VideoFile, observations: list[JsonObject]) -> NoReturn:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(anonymizer, "_persist_phi_region_proposals_unchecked", fail)

    with caplog.at_level("WARNING"):
        count = anonymizer._persist_phi_region_proposals(
            video,
            {"frame_observations": [_phi_observation()]},
        )

    assert count == 0
    assert "Failed to persist lx-anonymizer PHI region proposals" in caplog.text


@pytest.mark.unit
def test_verify_anonymizer_source_aborts_on_validated_hash_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source_video = tmp_path / "changed.mp4"
    source_video.write_bytes(b"changed-source")
    stat_result = source_video.stat()
    ctx = ImportContext(
        file_path=source_video,
        center_name="hash-mismatch-center",
        validated_raw_source_path=source_video,
        validated_raw_source_size_bytes=stat_result.st_size,
        validated_raw_source_mtime_ns=stat_result.st_mtime_ns,
        validated_raw_source_sha256="0" * 64,
        validated_raw_source_stream={"width": 640, "height": 480},
    )
    monkeypatch.setattr(
        video_anonymization,
        "get_stream_info",
        _stream_info_for_path,
    )
    quarantine_dir = tmp_path / "quarantine"
    monkeypatch.setattr(
        video_anonymization,
        "_quarantine_dir",
        _quarantine_dir_for(quarantine_dir),
    )

    with caplog.at_level("CRITICAL"):
        with pytest.raises(RuntimeError, match="hash differs"):
            video_anonymization._verify_anonymizer_source(
                ctx,
                source_video,
                video_hash="source-mismatch",
            )

    assert "video.anonymizer_source_integrity_mismatch" in caplog.text
    assert "quarantined_path" in caplog.text
    quarantined_files = list(
        quarantine_dir.glob("source-mismatch.anonymizer-input.sha256.*.mp4")
    )
    assert len(quarantined_files) == 1
    assert quarantined_files[0].read_bytes() == b"changed-source"


@pytest.mark.django_db
def test_anonymize_video_persists_phi_region_proposals_from_frame_cleaner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    center = Center.objects.create(
        name="phi-anonymize-video-center",
        display_name="PHI Anonymize Video Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="phi-video-anonymize")
    processor = _create_processor_with_roi("phi_anonymize_video_processor", center)
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source-video")
    video.ensure_local_raw_file = _local_raw_file_context(source_video)
    Frame.objects.create(
        video=video,
        frame_number=5,
        relative_path="frame_5.jpg",
        is_extracted=True,
    )
    output_dir = tmp_path / "anonymized"

    class FakeFrameCleaner:
        def clean_video(
            self,
            *,
            video_path: Path,
            endoscope_image_roi: dict[str, int],
            endoscope_data_roi_nested: dict[str, dict[str, int | None]],
            source_frame_rate: Fraction,
            output_path: Path,
        ) -> tuple[Path, JsonObject]:
            assert source_frame_rate == Fraction(30000, 1001)
            output_path.write_bytes(b"anonymized-video")
            return output_path, {
                "frame_observations": [_phi_observation()],
                "paper_evaluation_metrics": {
                    "runtime": {
                        "total_frames": 12,
                        "frames_processed": 1,
                        "anonymizer_seconds": 0.5,
                    },
                    "temporal_accumulation": {
                        "sensitive_frame_count": 1,
                        "phi_region_count": 1,
                    },
                },
            }

    monkeypatch.setattr(video_anonymization, "FrameCleaner", FakeFrameCleaner)
    monkeypatch.setattr(
        video_anonymization, "_ensure_ffmpeg_tools_on_path", _ensure_ffmpeg_tools_noop
    )
    monkeypatch.setattr(
        video_anonymization,
        "_processed_video_dir",
        _processed_video_dir_for(output_dir),
    )
    monkeypatch.setattr(
        video_anonymization,
        "get_stream_info",
        _stream_info_for_path,
    )
    monkeypatch.setattr(
        video_anonymization,
        "sensitive_meta_storage",
        _sensitive_meta_storage_noop,
    )

    ctx = _create_import_context(
        file_path=source_video,
        center=center,
        video=video,
        processor_name=processor.name,
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    anonymizer._frame_cleaning_available = True

    result_ctx = anonymizer.anonymize_video(ctx)

    assert result_ctx.anonymized_path == (
        output_dir / f"phi-video-anonymize.attempt-{ctx.attempt_id}.mp4"
    )
    assert result_ctx.anonymized_path is not None
    assert result_ctx.anonymized_path.read_bytes() == b"anonymized-video"
    assert FrameBoxAnnotation.objects.count() == 1
    video.refresh_from_db()
    assert video.meta is not None
    persisted_metrics = video.meta.get("paper_evaluation_metrics")
    assert isinstance(persisted_metrics, dict)
    runtime_metrics = persisted_metrics.get("runtime")
    assert isinstance(runtime_metrics, dict)
    assert runtime_metrics.get("total_frames") == 12
    temporal_metrics = persisted_metrics.get("temporal_accumulation")
    assert isinstance(temporal_metrics, dict)
    assert temporal_metrics.get("phi_region_count") == 1


@pytest.mark.django_db
def test_reanonymize_video_keeps_new_output_staged_until_finalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    center = Center.objects.create(
        name="staged-reanonymize-center",
        display_name="Staged Re-anonymize Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="staged-reanonymize-video",
    )
    processor = _create_processor_with_roi("staged_reanonymize_processor", center)
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source-video")
    output_dir = tmp_path / "anonymized"
    output_dir.mkdir()
    canonical_output = output_dir / "staged-reanonymize-video.mp4"
    canonical_output.write_bytes(b"previous-processed-video")

    class FakeFrameCleaner:
        def clean_video(
            self,
            *,
            video_path: Path,
            endoscope_image_roi: dict[str, int],
            endoscope_data_roi_nested: dict[str, dict[str, int | None]],
            source_frame_rate: Fraction,
            output_path: Path,
        ) -> tuple[Path, JsonObject]:
            assert source_frame_rate == Fraction(30000, 1001)
            output_path.write_bytes(b"fresh-anonymized-video")
            return output_path, {}

    monkeypatch.setattr(video_anonymization, "FrameCleaner", FakeFrameCleaner)
    monkeypatch.setattr(
        video_anonymization, "_ensure_ffmpeg_tools_on_path", _ensure_ffmpeg_tools_noop
    )
    monkeypatch.setattr(
        video_anonymization,
        "_processed_video_dir",
        _processed_video_dir_for(output_dir),
    )
    monkeypatch.setattr(
        video_anonymization,
        "get_stream_info",
        _stream_info_for_path,
    )
    monkeypatch.setattr(
        video_anonymization,
        "sensitive_meta_storage",
        _sensitive_meta_storage_noop,
    )

    ctx = _create_import_context(
        file_path=source_video,
        center=center,
        video=video,
        processor_name=processor.name,
    )
    ctx.retry = True
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    anonymizer._frame_cleaning_available = True

    result_ctx = anonymizer.anonymize_video(ctx)

    assert canonical_output.read_bytes() == b"previous-processed-video"
    assert result_ctx.anonymized_path is not None
    assert result_ctx.anonymized_path == (
        output_dir / f"staged-reanonymize-video.attempt-{ctx.attempt_id}.mp4"
    )
    assert result_ctx.anonymized_path.read_bytes() == b"fresh-anonymized-video"


@pytest.mark.django_db
def test_persist_paper_evaluation_metrics_rejects_non_json_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    center = Center.objects.create(
        name="paper-metrics-center",
        display_name="Paper Metrics Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="paper-metrics-video")
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)

    with caplog.at_level("WARNING"):
        saved = anonymizer._persist_paper_evaluation_metrics(
            video,
            {"paper_evaluation_metrics": {"runtime": {"total_seconds": float("nan")}}},
        )

    video.refresh_from_db()
    assert saved is False
    assert video.meta is None
    assert "does not allow NaN" in caplog.text


@pytest.mark.django_db
def test_anonymize_video_uses_local_source_path_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    center = Center.objects.create(
        name="local-source-path-center",
        display_name="Local Source Path Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="local-source-video")
    processor = _create_processor_with_roi("local_source_video_processor", center)
    video.ensure_local_raw_file = _raise_local_raw_file
    local_source = tmp_path / "local-source.mp4"
    local_source.write_bytes(b"local-source")
    fallback_source = tmp_path / "fallback.mp4"
    fallback_source.write_bytes(b"fallback-source")
    output_dir = tmp_path / "anonymized"
    cleaned_paths: list[Path] = []

    class FakeFrameCleaner:
        def clean_video(
            self,
            *,
            video_path: Path,
            endoscope_image_roi: dict[str, int],
            endoscope_data_roi_nested: dict[str, dict[str, int | None]],
            source_frame_rate: Fraction,
            output_path: Path,
        ) -> tuple[Path, JsonObject]:
            assert source_frame_rate == Fraction(30000, 1001)
            cleaned_paths.append(video_path)
            output_path.write_bytes(b"anonymized-video")
            return output_path, {}

    monkeypatch.setattr(video_anonymization, "FrameCleaner", FakeFrameCleaner)
    monkeypatch.setattr(
        video_anonymization, "_ensure_ffmpeg_tools_on_path", _ensure_ffmpeg_tools_noop
    )
    monkeypatch.setattr(
        video_anonymization,
        "_processed_video_dir",
        _processed_video_dir_for(output_dir),
    )
    monkeypatch.setattr(
        video_anonymization,
        "get_stream_info",
        _stream_info_for_path,
    )
    monkeypatch.setattr(
        video_anonymization,
        "sensitive_meta_storage",
        _sensitive_meta_storage_noop,
    )

    local_source_stat = local_source.stat()
    ctx = ImportContext(
        file_path=fallback_source,
        center_name=center.name,
        current_video=video,
        local_source_path=local_source,
        validated_raw_source_path=local_source,
        validated_raw_source_size_bytes=local_source_stat.st_size,
        validated_raw_source_mtime_ns=local_source_stat.st_mtime_ns,
        validated_raw_source_sha256=video_anonymization.sha256_file(local_source),
        validated_raw_source_stream={"width": 640, "height": 480},
        processor_name=processor.name,
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    anonymizer._frame_cleaning_available = True

    result_ctx = anonymizer.anonymize_video(ctx)

    assert cleaned_paths == [local_source]
    assert result_ctx.anonymizer_source_snapshot.get("path") == str(
        local_source.resolve()
    )
    assert (
        result_ctx.anonymizer_source_snapshot.get("sha256")
        == ctx.validated_raw_source_sha256
    )
    assert result_ctx.anonymized_path == (
        output_dir / f"local-source-video.attempt-{ctx.attempt_id}.mp4"
    )
    assert result_ctx.anonymized_path is not None
    assert result_ctx.anonymized_path.read_bytes() == b"anonymized-video"


@pytest.mark.django_db
def test_anonymizer_reuses_initialized_frame_cleaner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    center = Center.objects.create(
        name="reuse-frame-cleaner-center",
        display_name="Reuse FrameCleaner Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="reuse-frame-cleaner")
    processor = _create_processor_with_roi("reuse_frame_cleaner_processor", center)
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source-video")
    output_dir = tmp_path / "anonymized"
    frame_cleaner_instances: list[object] = []

    class FakeFrameCleaner:
        def __init__(self) -> None:
            frame_cleaner_instances.append(self)

        def clean_video(
            self,
            *,
            video_path: Path,
            endoscope_image_roi: dict[str, int],
            endoscope_data_roi_nested: dict[str, dict[str, int | None]],
            source_frame_rate: Fraction,
            output_path: Path,
        ) -> tuple[Path, JsonObject]:
            assert source_frame_rate == Fraction(30000, 1001)
            output_path.write_bytes(b"anonymized-video")
            return output_path, {}

    monkeypatch.setattr(video_anonymization, "FrameCleaner", FakeFrameCleaner)
    monkeypatch.setattr(
        video_anonymization, "_ensure_ffmpeg_tools_on_path", _ensure_ffmpeg_tools_noop
    )
    monkeypatch.setattr(
        video_anonymization,
        "_processed_video_dir",
        _processed_video_dir_for(output_dir),
    )
    monkeypatch.setattr(
        video_anonymization,
        "get_stream_info",
        _stream_info_for_path,
    )
    monkeypatch.setattr(
        video_anonymization,
        "sensitive_meta_storage",
        _sensitive_meta_storage_noop,
    )

    ctx = _create_import_context(
        file_path=source_video,
        center=center,
        video=video,
        processor_name=processor.name,
    )
    anonymizer = RealVideoAnonymizer()

    anonymizer.anonymize_video(ctx)

    assert len(frame_cleaner_instances) == 1


@pytest.mark.django_db
def test_anonymize_video_scales_processor_roi_to_source_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    center = Center.objects.create(
        name="scaled-roi-video-center",
        display_name="Scaled ROI Video Center",
    )
    video = VideoFile.objects.create(center=center, video_hash="scaled-roi-video")
    processor = _create_processor_with_roi("scaled_roi_video_processor", center)
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source-video")
    output_dir = tmp_path / "anonymized"
    observed_endoscope_rois: list[dict[str, int]] = []
    observed_sensitive_rois: list[dict[str, dict[str, int | None]]] = []

    class FakeFrameCleaner:
        def clean_video(
            self,
            *,
            video_path: Path,
            endoscope_image_roi: dict[str, int],
            endoscope_data_roi_nested: dict[str, dict[str, int | None]],
            source_frame_rate: Fraction,
            output_path: Path,
        ) -> tuple[Path, JsonObject]:
            assert source_frame_rate == Fraction(30000, 1001)
            observed_endoscope_rois.append(endoscope_image_roi)
            observed_sensitive_rois.append(endoscope_data_roi_nested)
            output_path.write_bytes(b"anonymized-video")
            return output_path, {}

    monkeypatch.setattr(video_anonymization, "FrameCleaner", FakeFrameCleaner)
    monkeypatch.setattr(
        video_anonymization, "_ensure_ffmpeg_tools_on_path", _ensure_ffmpeg_tools_noop
    )
    monkeypatch.setattr(
        video_anonymization,
        "_processed_video_dir",
        _processed_video_dir_for(output_dir),
    )
    monkeypatch.setattr(
        video_anonymization,
        "get_stream_info",
        _small_stream_info_for_path,
    )
    monkeypatch.setattr(
        video_anonymization,
        "sensitive_meta_storage",
        _sensitive_meta_storage_noop,
    )

    ctx = _create_import_context(
        file_path=source_video,
        center=center,
        video=video,
        processor_name=processor.name,
    )
    anonymizer = RealVideoAnonymizer.__new__(RealVideoAnonymizer)
    anonymizer._frame_cleaning_available = True

    anonymizer.anonymize_video(ctx)

    assert observed_endoscope_rois == [
        {
            "x": 183,
            "y": 0,
            "width": 450,
            "height": 480,
            "image_width": 640,
            "image_height": 480,
        }
    ]
    assert observed_sensitive_rois[0]["examination_date"] == {
        "x": 33,
        "y": 4,
        "width": 67,
        "height": 18,
    }
