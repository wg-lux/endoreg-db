from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from typing import Final, cast

from django.db import transaction
from django.utils import timezone

from endoreg_db.config.env import (
    get_media_operation_segment_update_grace_seconds,
    get_media_operation_stream_lease_seconds,
)
from endoreg_db.exceptions import MediaOperationDeferred
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.schemas import (
    FfmpegActiveStreamThrottleState,
    FfmpegStreamThrottleState,
    FfmpegStreamThrottleStatePayload,
    MediaOperationLeaseSummary,
    MediaOperationLeaseSummaryPayload,
    StreamThrottleMode,
    dump_ffmpeg_stream_throttle_state,
    dump_media_operation_lease_summary,
)

logger = logging.getLogger(__name__)

FFMPEG_STREAM_THROTTLE_NORMAL: Final[StreamThrottleMode] = "normal"
FFMPEG_STREAM_THROTTLE_STREAMING: Final[StreamThrottleMode] = "streaming"


def expire_media_operation_leases(*, video_id: int | None = None) -> int:
    queryset = MediaOperationLease.objects.filter(expires_at__lte=timezone.now())
    if video_id is not None:
        queryset = queryset.filter(video_id=int(video_id))
    deleted_count, _ = queryset.delete()
    return int(deleted_count)


def create_video_stream_lease(
    video: object,
    *,
    file_type: str,
    ttl_seconds: int | None = None,
) -> MediaOperationLease | None:
    if not isinstance(video, VideoFile) or getattr(video, "pk", None) is None:
        return None
    ttl = (
        get_media_operation_stream_lease_seconds()
        if ttl_seconds is None
        else max(1, int(ttl_seconds))
    )
    expires_at = timezone.now() + timedelta(seconds=ttl)
    normalized_file_type = str(file_type).strip()
    if not normalized_file_type:
        raise ValueError("file_type must not be empty")
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=int(video.pk))
        existing = (
            MediaOperationLease.objects.select_for_update()
            .filter(
                video=locked_video,
                lease_type=MediaOperationLease.LEASE_STREAM,
                metadata__file_type=normalized_file_type,
            )
            .order_by("-expires_at", "pk")
            .first()
        )
        if existing is not None:
            existing.expires_at = expires_at
            existing.save(update_fields=["expires_at"])
            return existing
        return MediaOperationLease.objects.create(
            video=locked_video,
            lease_type=MediaOperationLease.LEASE_STREAM,
            expires_at=expires_at,
            metadata={"file_type": normalized_file_type},
        )


def create_video_segment_update_lease(
    video: VideoFile,
    *,
    ttl_seconds: int | None = None,
) -> MediaOperationLease:
    ttl = (
        get_media_operation_segment_update_grace_seconds()
        if ttl_seconds is None
        else max(1, int(ttl_seconds))
    )
    expires_at = timezone.now() + timedelta(seconds=ttl)
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=int(video.pk))
        return MediaOperationLease.objects.create(
            video=locked_video,
            lease_type=MediaOperationLease.LEASE_SEGMENT_UPDATE,
            expires_at=expires_at,
            metadata={"source": "segment_validation"},
        )


def release_media_operation_lease(lease: MediaOperationLease | None) -> None:
    if lease is None or lease.pk is None:
        return
    try:
        MediaOperationLease.objects.filter(pk=lease.pk).delete()
    except Exception:
        logger.exception("Failed to release media operation lease %s.", lease.pk)


def wrap_iterator_with_media_lease(
    chunks: Iterable[bytes],
    lease: MediaOperationLease,
) -> Iterator[bytes]:
    try:
        yield from chunks
    finally:
        release_media_operation_lease(lease)


def get_ffmpeg_stream_throttle_state() -> FfmpegStreamThrottleStatePayload:
    expired_leases = expire_media_operation_leases()
    checked_at = timezone.now()
    active_stream_leases = MediaOperationLease.objects.filter(
        lease_type=MediaOperationLease.LEASE_STREAM,
        expires_at__gt=checked_at,
    )
    active_stream_lease_count = active_stream_leases.count()
    next_stream_lease_expiry = (
        active_stream_leases.order_by("expires_at")
        .values_list("expires_at", flat=True)
        .first()
    )

    mode = (
        FFMPEG_STREAM_THROTTLE_STREAMING
        if active_stream_lease_count > 0
        else FFMPEG_STREAM_THROTTLE_NORMAL
    )
    if isinstance(next_stream_lease_expiry, datetime):
        state = FfmpegActiveStreamThrottleState(
            mode=mode,
            active_stream_leases=active_stream_lease_count,
            expired_leases=expired_leases,
            checked_at=checked_at,
            next_stream_lease_expiry=next_stream_lease_expiry,
        )
    else:
        state = FfmpegStreamThrottleState(
            mode=mode,
            active_stream_leases=active_stream_lease_count,
            expired_leases=expired_leases,
            checked_at=checked_at,
        )
    return dump_ffmpeg_stream_throttle_state(state)


def active_media_operation_lease_summary(
    video_id: int,
) -> list[MediaOperationLeaseSummaryPayload]:
    expire_media_operation_leases(video_id=int(video_id))
    rows = (
        MediaOperationLease.objects.filter(
            video_id=int(video_id),
            expires_at__gt=timezone.now(),
        )
        .order_by("lease_type", "expires_at")
        .values("lease_type", "expires_at")
    )
    summary: list[MediaOperationLeaseSummaryPayload] = []
    for row in rows:
        lease = MediaOperationLeaseSummary(
            lease_type=cast(str, row["lease_type"]),
            expires_at=cast(datetime, row["expires_at"]),
        )
        summary.append(dump_media_operation_lease_summary(lease))
    return summary


def video_has_active_media_operation_leases(video_id: int) -> bool:
    expire_media_operation_leases(video_id=int(video_id))
    return MediaOperationLease.objects.filter(
        video_id=int(video_id),
        expires_at__gt=timezone.now(),
    ).exists()


def defer_if_video_media_busy(
    *,
    video_id: int,
    history: VideoProcessingHistory | None = None,
) -> None:
    active_leases = active_media_operation_lease_summary(int(video_id))
    if not active_leases:
        return

    detail = (
        "Post-validation rebuild delayed because media operation leases are active: "
        f"{active_leases}"
    )
    if history is not None:
        history.details = detail
        history.save(update_fields=["details"])
    raise MediaOperationDeferred(detail)


def create_segment_update_lease_on_commit(video: VideoFile) -> None:
    video_id = int(video.pk)

    def _create_lease_after_commit() -> None:
        try:
            current_video = VideoFile.objects.get(pk=video_id)
        except VideoFile.DoesNotExist:
            return
        create_video_segment_update_lease(current_video)

    transaction.on_commit(_create_lease_after_commit)
