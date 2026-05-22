from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass

from django.db import transaction

from endoreg_db.config.env import (
    celery_broker_transport_error,
    celery_frame_extraction_requires_secure_transport,
    get_celery_frame_extraction_queue,
)
from endoreg_db.models import Frame, FrameExtractionRequest, VideoFile
from endoreg_db.services.video_files import extract_video_frame_range

logger = logging.getLogger(__name__)

REQUEST_STATUS_QUEUED = "queued"
REQUEST_STATUS_ALREADY_QUEUED = "already_queued"
REQUEST_STATUS_RUNNING = "running"
REQUEST_STATUS_FAILED = "failed"
REQUEST_STATUS_COMPLETED = "completed"

ACTIVE_REQUEST_STATUSES = {
    FrameExtractionRequest.STATUS_PENDING,
    FrameExtractionRequest.STATUS_RUNNING,
}


@dataclass(frozen=True)
class FrameExtractionDispatchResult:
    request_id: int
    task_id: str
    status: str
    video_id: int
    frame_number: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _expected_relative_path(frame_number: int) -> str:
    return f"frame_{frame_number:07d}.jpg"


def get_or_create_frame_record(*, video: VideoFile, frame_number: int) -> Frame:
    frame = (
        Frame.objects.select_related("video")
        .filter(video=video, frame_number=frame_number)
        .first()
    )
    if frame is not None:
        return frame
    frame, _created = Frame.objects.get_or_create(
        video=video,
        frame_number=frame_number,
        defaults={
            "relative_path": _expected_relative_path(frame_number),
            "is_extracted": False,
        },
    )
    return frame


def is_frame_file_available(*, frame: Frame) -> bool:
    frame_path = frame.file_path
    return frame_path.exists() and frame_path.is_file()


def _ensure_frame_extraction_broker_transport_allowed() -> None:
    error = celery_broker_transport_error(
        require_secure_transport=celery_frame_extraction_requires_secure_transport(),
        workload="Frame extraction Celery",
    )
    if error is None:
        return
    raise RuntimeError(error)


def request_frame_extraction(
    *,
    video: VideoFile,
    frame_number: int,
) -> FrameExtractionDispatchResult:
    frame = get_or_create_frame_record(video=video, frame_number=frame_number)
    if is_frame_file_available(frame=frame):
        if not frame.is_extracted:
            Frame.objects.filter(pk=frame.pk, is_extracted=False).update(
                is_extracted=True
            )
            frame.is_extracted = True
        request = (
            FrameExtractionRequest.objects.filter(
                video=video,
                frame_number=frame_number,
            )
            .order_by("-requested_at")
            .first()
        )
        return FrameExtractionDispatchResult(
            request_id=request.pk if request is not None else 0,
            task_id=request.task_id if request is not None else "",
            status=REQUEST_STATUS_COMPLETED,
            video_id=video.pk,
            frame_number=frame_number,
        )

    queue = get_celery_frame_extraction_queue()
    task_id = str(uuid.uuid4())
    with transaction.atomic():
        request, created = (
            FrameExtractionRequest.objects.select_for_update().get_or_create(
                video=video,
                frame_number=frame_number,
                defaults={
                    "status": FrameExtractionRequest.STATUS_PENDING,
                    "task_id": task_id,
                },
            )
        )

        if not created:
            if request.status == FrameExtractionRequest.STATUS_FAILURE:
                return FrameExtractionDispatchResult(
                    request_id=request.pk,
                    task_id=request.task_id,
                    status=REQUEST_STATUS_FAILED,
                    video_id=video.pk,
                    frame_number=frame_number,
                )
            if request.status == FrameExtractionRequest.STATUS_RUNNING:
                return FrameExtractionDispatchResult(
                    request_id=request.pk,
                    task_id=request.task_id,
                    status=REQUEST_STATUS_RUNNING,
                    video_id=video.pk,
                    frame_number=frame_number,
                )
            if request.status in ACTIVE_REQUEST_STATUSES:
                return FrameExtractionDispatchResult(
                    request_id=request.pk,
                    task_id=request.task_id,
                    status=REQUEST_STATUS_ALREADY_QUEUED,
                    video_id=video.pk,
                    frame_number=frame_number,
                )
            request.mark_pending(task_id=task_id)

    try:
        from endoreg_db.tasks import run_frame_extraction_request_task

        _ensure_frame_extraction_broker_transport_allowed()
        async_result = run_frame_extraction_request_task.apply_async(
            kwargs={
                "request_id": request.pk,
                "video_id": int(video.pk),
                "frame_number": int(frame_number),
            },
            queue=queue,
            routing_key=queue,
            task_id=task_id,
        )
        if request.task_id != str(async_result.id):
            request.task_id = str(async_result.id)
            request.save(update_fields=["task_id"])
        return FrameExtractionDispatchResult(
            request_id=request.pk,
            task_id=str(async_result.id),
            status=REQUEST_STATUS_QUEUED,
            video_id=video.pk,
            frame_number=frame_number,
        )
    except Exception as exc:
        logger.exception(
            "Celery dispatch failed for frame extraction video=%s frame=%s",
            video.pk,
            frame_number,
        )
        request.mark_failure(str(exc))
        return FrameExtractionDispatchResult(
            request_id=request.pk,
            task_id=request.task_id,
            status=REQUEST_STATUS_FAILED,
            video_id=video.pk,
            frame_number=frame_number,
        )


def run_frame_extraction_request(
    *,
    request_id: int,
    video_id: int,
    frame_number: int,
) -> bool:
    request = FrameExtractionRequest.objects.get(pk=request_id)
    request.mark_running()
    try:
        video = VideoFile.objects.get(pk=video_id)
        frame = get_or_create_frame_record(video=video, frame_number=frame_number)
        if not is_frame_file_available(frame=frame):
            extract_video_frame_range(
                video,
                start_frame=frame_number,
                end_frame=frame_number + 1,
                overwrite=False,
            )
            frame.refresh_from_db()
        if not is_frame_file_available(frame=frame):
            raise RuntimeError(
                "Frame extraction completed without creating the requested frame file."
            )
        if not frame.is_extracted:
            Frame.objects.filter(pk=frame.pk, is_extracted=False).update(
                is_extracted=True
            )
        request.mark_success()
        return True
    except Exception as exc:
        request.mark_failure(str(exc))
        raise
