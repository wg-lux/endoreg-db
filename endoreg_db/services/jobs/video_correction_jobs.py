from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

import cv2
from django.conf import settings
from django.db import transaction
from django.db.models.fields.files import FieldFile
from pydantic import BaseModel, ConfigDict

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.services.hls_media import materialize_video_hls
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.jobs.stale_recovery import (
    recover_stale_video_processing_history,
)
from endoreg_db.services.media_operation_gate import defer_if_video_media_busy
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils import ffmpeg_wrapper, paths as path_utils
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    ensure_directory,
    safe_unlink_file,
)
from endoreg_db.utils.storage import ensure_local_file, save_local_file

logger = logging.getLogger(__name__)

VIDEO_ANONYMIZATION_CORRECTION_JOB_KIND = "video_anonymization_correction"


class VideoCorrectionRoi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    width: int
    height: int


class VideoCorrectionRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["device", "custom"]
    device_name: str
    roi: VideoCorrectionRoi | None = None


class VideoAnonymizationCorrectionJobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_kind: Literal["video_anonymization_correction"] = (
        VIDEO_ANONYMIZATION_CORRECTION_JOB_KIND
    )
    strategy: Literal["detector_assisted", "processor_region"]
    processing_method: Literal["streaming", "direct"]
    region: VideoCorrectionRegion
    human_review_required: Literal[True]
    apply_all_frames: Literal[True]
    queue: str


@dataclass(frozen=True)
class VideoAnonymizationCorrectionDispatchResult:
    video_id: int
    status: str
    queue: str
    task_id: str
    history_id: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


try:
    from lx_anonymizer import FrameCleaner as _FrameCleaner  # type: ignore[reportMissingTypeStubs]

    FrameCleaner = cast(Any, _FrameCleaner)
except ImportError as exc:  # pragma: no cover - dependency-light test environments
    _FRAME_CLEANER_IMPORT_ERROR = exc

    class _UnavailableFrameCleaner:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError(
                "lx_anonymizer FrameCleaner is unavailable"
            ) from _FRAME_CLEANER_IMPORT_ERROR

    FrameCleaner = cast(Any, _UnavailableFrameCleaner)


def _coerce_frame_number(value: object) -> int:
    if isinstance(value, int | float | str):
        return int(value)
    raise ValueError(f"Invalid frame number: {value!r}")


def _video_hash(video: VideoFile) -> str:
    return str(cast(Any, video).video_hash)


def _output_path(video: VideoFile, strategy: str) -> Path:
    output_dir = ensure_directory(
        path_utils.EndoregPathsModel.from_environment().anonym_video
    )
    return output_dir / f"{_video_hash(video)}_{strategy.replace('_', '-')}.mp4"


def _part_output_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.part{output_path.suffix}")


def _promote_output(temp_path: Path, final_path: Path) -> Path:
    if not temp_path.is_file():
        raise FileNotFoundError(f"Temporary processed output missing: {temp_path}")
    if temp_path.stat().st_size <= 0:
        raise RuntimeError(f"Temporary processed output is empty: {temp_path}")
    ensure_directory(final_path.parent)
    atomic_move_file(source=temp_path, destination=final_path)
    return final_path


def update_processed_file(video: VideoFile, output_path: Path) -> str:
    processed_file: FieldFile = video.processed_file
    if not hasattr(processed_file, "field"):
        processed_file.name = str(output_path)
        cast(Any, video).save(update_fields=["processed_file"])
        return str(processed_file.name)

    canonical_path = (
        path_utils.EndoregPathsModel.from_environment().anonym_video / output_path.name
    )
    stored_name = save_local_file(
        processed_file,
        output_path,
        name=path_utils.to_storage_relative(canonical_path),
        save=False,
        overwrite=True,
    )
    cast(Any, video).save(update_fields=["processed_file"])
    safe_unlink_file(output_path, missing_ok=True)
    sync_video_streamable_artifacts(
        video,
        include_raw=False,
        include_processed=True,
        save=True,
    )
    return stored_name


def mask_video_with_detector_compat(
    frame_cleaner: Any, input_video: Path, output_video: Path
) -> dict[str, object]:
    capture = cv2.VideoCapture(str(input_video))
    if not capture.isOpened():
        raise RuntimeError("Could not decode input video for detector masking")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if width <= 0 or height <= 0 or fps <= 0:
        capture.release()
        raise RuntimeError("Input video has invalid dimensions or frame rate")

    ensure_directory(output_video.parent)
    frames_processed = 0
    frames_with_redactions = 0
    redactions_applied = 0
    with tempfile.TemporaryDirectory(
        prefix="endoreg-detector-mask-", dir=output_video.parent
    ) as temp_dir:
        video_only = Path(temp_dir) / "masked-video.mp4"
        writer = cv2.VideoWriter(
            str(video_only),
            cv2.VideoWriter_fourcc(*"mp4v"),  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue,reportUnknownArgumentType]
            fps,
            (width, height),
        )
        if not writer.isOpened():
            capture.release()
            raise RuntimeError("Could not initialize detector masking writer")
        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                raw_regions = frame_cleaner._detect_phi_regions_for_frame(frame)
                valid_regions = 0
                for item in raw_regions:
                    if not isinstance(item, Mapping):
                        continue
                    region_item = cast(Mapping[str, object], item)
                    try:
                        x1, y1, x2, y2 = (
                            _coerce_frame_number(region_item[key])
                            for key in ("x1", "y1", "x2", "y2")
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    x1, x2 = max(0, x1), min(width, x2)
                    y1, y2 = max(0, y1), min(height, y2)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    frame[y1:y2, x1:x2] = 0
                    valid_regions += 1
                writer.write(frame)
                frames_processed += 1
                if valid_regions:
                    frames_with_redactions += 1
                    redactions_applied += valid_regions
        finally:
            capture.release()
            writer.release()

        if frames_processed == 0 or not video_only.is_file():
            raise RuntimeError("Detector masking produced no video frames")
        ffmpeg_executable = ffmpeg_wrapper.resolve_ffmpeg_executable()
        if ffmpeg_executable is None:
            raise RuntimeError("ffmpeg executable is not available")
        command = [
            ffmpeg_executable,
            "-nostdin",
            "-y",
            "-i",
            str(video_only),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-an",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Could not finalize detector video: {exc.stderr}"
            ) from exc

    return {
        "strategy": "detector_assisted",
        "frames_processed": frames_processed,
        "frames_with_redactions": frames_with_redactions,
        "redactions_applied": redactions_applied,
        "review_required": True,
    }


def apply_video_anonymization_strategy(
    *,
    frame_cleaner: Any,
    raw_path: Path,
    output_path: Path,
    config: VideoAnonymizationCorrectionJobConfig,
) -> dict[str, object]:
    if config.strategy == "detector_assisted":
        mask_method = getattr(frame_cleaner, "mask_video_with_phi_detector", None)
        if callable(mask_method):
            summary = mask_method(input_video=raw_path, output_video=output_path)
            to_dict = getattr(summary, "to_dict", None)
            return cast(dict[str, object], to_dict() if callable(to_dict) else {})
        return mask_video_with_detector_compat(frame_cleaner, raw_path, output_path)

    if config.region.mode == "device":
        frame_cleaner.mask_application.device_name = config.region.device_name
        mask_config = frame_cleaner.mask_application._load_mask()
    else:
        if config.region.roi is None:
            raise ValueError("region.roi is required for a custom processor region")
        mask_config = frame_cleaner.mask_application.create_mask_config_from_roi(
            endoscope_image_roi=config.region.roi.model_dump(mode="json")
        )
    success = frame_cleaner.mask_application.mask_video_streaming(
        input_video=raw_path,
        mask_config=mask_config,
        output_video=output_path,
        use_named_pipe=config.processing_method == "streaming",
    )
    if not success:
        raise RuntimeError("Processor-region masking failed")
    return {
        "frames_processed": None,
        "frames_with_redactions": None,
        "redactions_applied": None,
    }


def _active_history(video: VideoFile) -> VideoProcessingHistory | None:
    return (
        VideoProcessingHistory.objects.filter(
            video=video,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status__in=(
                VideoProcessingHistory.STATUS_PENDING,
                VideoProcessingHistory.STATUS_RUNNING,
            ),
            config__job_kind=VIDEO_ANONYMIZATION_CORRECTION_JOB_KIND,
        )
        .order_by("created_at")
        .first()
    )


def dispatch_video_anonymization_correction(
    video: VideoFile,
    payload: Mapping[str, object],
) -> VideoAnonymizationCorrectionDispatchResult:
    queue = queue_for_job_kind(HeavyJobKind.VIDEO_ANONYMIZATION_CORRECTION)
    config = VideoAnonymizationCorrectionJobConfig.model_validate(
        {**payload, "queue": queue}
    )
    task_id = str(uuid.uuid4())
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
        active = _active_history(locked_video)
        if active is not None and recover_stale_video_processing_history(
            active,
            job_name="video anonymization correction",
        ):
            active = None
        if active is not None:
            return VideoAnonymizationCorrectionDispatchResult(
                video_id=int(video.pk),
                status=(
                    "running"
                    if active.status == VideoProcessingHistory.STATUS_RUNNING
                    else "already_queued"
                ),
                queue=queue,
                task_id=active.task_id,
                history_id=int(active.pk),
            )
        history = VideoProcessingHistory.objects.create(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_MASKING,
            status=VideoProcessingHistory.STATUS_PENDING,
            task_id=task_id,
            config=config.model_dump(mode="json"),
        )

    from endoreg_db.tasks import run_video_anonymization_correction_task

    if bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)):
        run_video_anonymization_correction_task.apply(
            args=(int(video.pk), int(history.pk))
        )
        history.refresh_from_db()
        return VideoAnonymizationCorrectionDispatchResult(
            video_id=int(video.pk),
            status=str(history.status),
            queue=queue,
            task_id=history.task_id,
            history_id=int(history.pk),
        )

    try:
        ensure_secure_transport_for_job_kind(
            HeavyJobKind.VIDEO_ANONYMIZATION_CORRECTION
        )
        async_result = run_video_anonymization_correction_task.apply_async(
            args=(int(video.pk), int(history.pk)),
            queue=queue,
            routing_key=queue,
        )
    except Exception as exc:
        history.mark_failure(str(exc))
        raise
    if str(async_result.id) != task_id:
        history.task_id = str(async_result.id)
        history.save(update_fields=["task_id"])
    return VideoAnonymizationCorrectionDispatchResult(
        video_id=int(video.pk),
        status="queued",
        queue=queue,
        task_id=str(async_result.id),
        history_id=int(history.pk),
    )


def run_video_anonymization_correction(
    video_id: int,
    history_id: int,
) -> dict[str, object]:
    history = VideoProcessingHistory.objects.get(
        pk=int(history_id), video_id=int(video_id)
    )
    if history.status == VideoProcessingHistory.STATUS_SUCCESS:
        return {
            "video_id": int(video_id),
            "history_id": int(history_id),
            "status": "success",
            "output_file": history.output_file,
        }

    config = VideoAnonymizationCorrectionJobConfig.model_validate(history.config)
    history.mark_running()
    defer_if_video_media_busy(video_id=int(video_id), history=history)

    video = VideoFile.objects.get(pk=int(video_id))
    output_path = _output_path(video, config.strategy)
    temp_output_path = _part_output_path(output_path)
    safe_unlink_file(temp_output_path, missing_ok=True)

    try:
        if not video.raw_file or not getattr(video.raw_file, "name", None):
            raise FileNotFoundError(
                f"Raw video file not found for correction: {_video_hash(video)}"
            )
        started_at = time.perf_counter()
        frame_cleaner = FrameCleaner()
        with ensure_local_file(video.raw_file) as raw_path:
            run_summary = apply_video_anonymization_strategy(
                frame_cleaner=frame_cleaner,
                raw_path=raw_path,
                output_path=temp_output_path,
                config=config,
            )
        processing_time = time.perf_counter() - started_at
        stored_name = update_processed_file(
            video, _promote_output(temp_output_path, output_path)
        )
        hls_result = materialize_video_hls(
            int(video_id), artifact_kind="processed", force=True
        )
        if hls_result.status != "ready":
            raise RuntimeError(
                f"Processed HLS materialization ended with {hls_result.status}."
            )
        summary: dict[str, object] = {
            **run_summary,
            "strategy": config.strategy,
            "processing_time": processing_time,
            "review_required": True,
            "output_file": stored_name,
            "hls_status": hls_result.status,
            "hls_segment_count": hls_result.segment_count,
        }
        history.mark_success(
            output_file=stored_name,
            details=json.dumps(summary, sort_keys=True),
        )
        return {
            "video_id": int(video_id),
            "history_id": int(history_id),
            "status": "success",
            **summary,
        }
    except Exception as exc:
        safe_unlink_file(temp_output_path, missing_ok=True)
        history.mark_failure(str(exc))
        logger.error(
            "Anonymization correction job failed for video %s: %s",
            video_id,
            exc,
            exc_info=True,
        )
        raise
