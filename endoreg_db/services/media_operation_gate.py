from __future__ import annotations

import logging
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Event, Thread
from typing import Final, cast
from uuid import UUID

from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from endoreg_db.config.env import (
    get_media_operation_segment_update_grace_seconds,
    get_media_operation_stream_lease_seconds,
)
from endoreg_db.exceptions import MediaOperationDeferred
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.utils.structured_logging import emit_structured_event
from lx_dtypes.models.contracts.media_streaming import (
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


class MediaOperationLeaseType(StrEnum):
    STREAM = MediaOperationLease.LEASE_STREAM
    SEGMENT_UPDATE = MediaOperationLease.LEASE_SEGMENT_UPDATE
    TRANSCODE = MediaOperationLease.LEASE_TRANSCODE
    ARTIFACT_WRITE = MediaOperationLease.LEASE_ARTIFACT_WRITE


@dataclass(frozen=True, slots=True)
class TranscodeLeaseClaim:
    video_id: int
    token: UUID
    heartbeat_failed: Event = field(default_factory=Event, compare=False, repr=False)


_transcode_claim: ContextVar[TranscodeLeaseClaim | None] = ContextVar(
    "video_transcode_claim", default=None
)
_artifact_writer: ContextVar[tuple[int, UUID] | None] = ContextVar(
    "video_artifact_writer", default=None
)


def _blocking_leases() -> Q:
    return Q(expires_at__gt=timezone.now()) | Q(
        lease_type=MediaOperationLease.LEASE_ARTIFACT_WRITE
    )


@dataclass(frozen=True, slots=True)
class MediaOperationLeaseAcquisition:
    video_id: int
    lease_type: MediaOperationLeaseType
    expires_at: datetime
    metadata: dict[str, object]
    renew_matching: bool = False

    def __post_init__(self) -> None:
        if self.video_id <= 0:
            raise ValueError("video_id must be positive")
        if not timezone.is_aware(self.expires_at) or self.expires_at <= timezone.now():
            raise ValueError("expires_at must be timezone-aware and in the future")


def acquire_media_operation_lease(
    *, request: MediaOperationLeaseAcquisition
) -> MediaOperationLease:
    """Acquire a lease through the VideoFile row used by cleanup authorization.

    An outstanding storage cleanup authorization is an exclusive barrier: no
    new playback or segment-update lease may start until that rotation reaches
    a terminal state.
    """

    from endoreg_db.models.hub.storage_placement import (
        StorageRotation,
        StorageRotationCleanupReceipt,
    )

    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=request.video_id)
        cleanup_pending = StorageRotationCleanupReceipt.objects.filter(
            rotation__source_placement__media_lease_video=locked_video,
            rotation__state__in=[
                StorageRotation.State.COMMITTED,
                StorageRotation.State.CLEANUP_DEFERRED,
            ],
        ).exists()
        if cleanup_pending:
            raise MediaOperationDeferred(
                "Media operation lease delayed by an authorized storage cleanup."
            )
        active = MediaOperationLease.objects.filter(
            _blocking_leases(), video=locked_video
        )
        if active.filter(
            lease_type__in=[
                MediaOperationLease.LEASE_TRANSCODE,
                MediaOperationLease.LEASE_ARTIFACT_WRITE,
            ]
        ).exists():
            raise MediaOperationDeferred("An exclusive video transcode is active.")
        exclusive = request.lease_type in {
            MediaOperationLeaseType.TRANSCODE,
            MediaOperationLeaseType.ARTIFACT_WRITE,
        }
        if exclusive and active.exists():
            raise MediaOperationDeferred(
                "Video transcode is waiting for active media operations."
            )
        if exclusive and request.renew_matching:
            raise ValueError(
                "Exclusive transcode ownership cannot be renewed by acquisition."
            )
        metadata = dict(request.metadata)
        if request.renew_matching:
            existing = (
                MediaOperationLease.objects.select_for_update()
                .filter(
                    video=locked_video,
                    lease_type=request.lease_type.value,
                    metadata=metadata,
                )
                .order_by("-expires_at", "pk")
                .first()
            )
            if existing is not None:
                existing.expires_at = request.expires_at
                existing.save(update_fields=["expires_at", "updated_at"])
                return existing
        return MediaOperationLease.objects.create(
            video=locked_video,
            lease_type=request.lease_type.value,
            expires_at=request.expires_at,
            metadata=metadata,
        )


def acquire_video_transcode_lease(
    *, video_id: int, ttl_seconds: int = 120
) -> TranscodeLeaseClaim:
    if ttl_seconds < 3:
        raise ValueError("Transcode lease lifetime must be at least three seconds.")
    lease = acquire_media_operation_lease(
        request=MediaOperationLeaseAcquisition(
            video_id=video_id,
            lease_type=MediaOperationLeaseType.TRANSCODE,
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
            metadata={},
        )
    )
    return TranscodeLeaseClaim(video_id=video_id, token=lease.token)


def assert_video_transcode_lease(claim: TranscodeLeaseClaim) -> None:
    if (
        claim.heartbeat_failed.is_set()
        or not MediaOperationLease.objects.filter(
            video_id=claim.video_id,
            token=claim.token,
            lease_type=MediaOperationLease.LEASE_TRANSCODE,
            expires_at__gt=timezone.now(),
        ).exists()
    ):
        raise MediaOperationDeferred("Exclusive video transcode ownership was lost.")


def assert_video_not_transcoding(video_id: int) -> None:
    """Caller holds the VideoFile row lock until its state mutation commits."""
    if MediaOperationLease.objects.filter(
        _blocking_leases(),
        video_id=video_id,
        lease_type__in=[
            MediaOperationLease.LEASE_TRANSCODE,
            MediaOperationLease.LEASE_ARTIFACT_WRITE,
        ],
    ).exists():
        raise MediaOperationDeferred("An exclusive video transcode is active.")


def _renew_exclusive_media_lease(
    *, video_id: int, token: UUID, lease_type: MediaOperationLeaseType, ttl_seconds: int
) -> None:
    if ttl_seconds < 3:
        raise ValueError(
            "Exclusive media lease lifetime must be at least three seconds."
        )
    # Cleanup/publication can hold the video row while hashing large artifacts.
    # Renewal owns only this token's lease row and never waits for that video row.
    # Check the clock after acquiring the lease lock: a delayed renewal must not
    # revive an expired token. Token, video and type all remain fenced.
    with transaction.atomic():
        lease = (
            MediaOperationLease.objects.select_for_update(nowait=True)
            .filter(video_id=video_id, token=token, lease_type=lease_type.value)
            .first()
        )
        now = timezone.now()
        if lease is None or lease.expires_at <= now:
            raise MediaOperationDeferred(
                "Exclusive media operation ownership was lost."
            )
        updated = MediaOperationLease.objects.filter(
            pk=lease.pk,
            token=token,
            video_id=video_id,
            lease_type=lease_type.value,
            expires_at__gt=now,
        ).update(expires_at=now + timedelta(seconds=ttl_seconds), updated_at=now)
        if updated != 1:
            raise MediaOperationDeferred(
                "Exclusive media operation ownership was lost."
            )


def renew_video_transcode_lease(
    claim: TranscodeLeaseClaim, *, ttl_seconds: int = 120
) -> None:
    if claim.heartbeat_failed.is_set():
        raise MediaOperationDeferred("Exclusive video transcode ownership was lost.")
    _renew_exclusive_media_lease(
        video_id=claim.video_id,
        token=claim.token,
        lease_type=MediaOperationLeaseType.TRANSCODE,
        ttl_seconds=ttl_seconds,
    )


def renew_video_artifact_write_lease(
    *, video_id: int, token: UUID, ttl_seconds: int = 120
) -> None:
    _renew_exclusive_media_lease(
        video_id=video_id,
        token=token,
        lease_type=MediaOperationLeaseType.ARTIFACT_WRITE,
        ttl_seconds=ttl_seconds,
    )


def release_video_transcode_lease(claim: TranscodeLeaseClaim) -> None:
    with transaction.atomic():
        VideoFile.objects.select_for_update().get(pk=claim.video_id)
        MediaOperationLease.objects.filter(
            video_id=claim.video_id,
            token=claim.token,
            lease_type=MediaOperationLease.LEASE_TRANSCODE,
        ).delete()


@contextmanager
def video_transcode_publication(claim: TranscodeLeaseClaim) -> Generator[VideoFile]:
    """Fence the brief database publication, never encoding or artifact staging."""
    with transaction.atomic():
        video = VideoFile.objects.select_for_update().get(pk=claim.video_id)
        assert_video_transcode_lease(claim)
        scope = _transcode_claim.set(claim)
        try:
            yield video
            assert_video_transcode_lease(claim)
        finally:
            _transcode_claim.reset(scope)


@contextmanager
def video_transcode_lease(
    *,
    video_id: int,
    ttl_seconds: int = 120,
    claim: TranscodeLeaseClaim | None = None,
) -> Generator[TranscodeLeaseClaim]:
    """Scope trusted nested media work; standalone callers also own a heartbeat."""
    owned = claim is None
    active_claim = claim or acquire_video_transcode_lease(
        video_id=video_id, ttl_seconds=ttl_seconds
    )
    if active_claim.video_id != video_id:
        raise ValueError("Transcode lease does not belong to this video.")
    assert_video_transcode_lease(active_claim)
    scope = _transcode_claim.set(active_claim)
    stop = Event()

    def heartbeat() -> None:
        while not stop.wait(ttl_seconds / 3):
            close_old_connections()
            try:
                renew_video_transcode_lease(active_claim, ttl_seconds=ttl_seconds)
            except Exception as exc:
                active_claim.heartbeat_failed.set()
                emit_structured_event(
                    logger,
                    "video_transcode_lease_heartbeat_failed",
                    level=logging.ERROR,
                    video_id=video_id,
                    error_type=type(exc).__name__,
                )
                return
            finally:
                close_old_connections()

    thread = Thread(
        target=heartbeat, name=f"video-transcode-lease-{video_id}", daemon=True
    )
    if owned:
        thread.start()
    try:
        yield active_claim
        assert_video_transcode_lease(active_claim)
    finally:
        _transcode_claim.reset(scope)
        if owned:
            stop.set()
            thread.join(timeout=5)
            if thread.is_alive():
                active_claim.heartbeat_failed.set()
                raise RuntimeError("Transcode heartbeat did not stop; lease retained.")
            release_video_transcode_lease(active_claim)


@contextmanager
def video_segment_mutation(*, video_id: int) -> Generator[VideoFile]:
    """Admit segment writes before mutation under the same publication row lock."""
    with transaction.atomic():
        video = VideoFile.objects.select_for_update().get(pk=video_id)
        create_video_segment_update_lease(video)
        yield video


def _assert_artifact_writer(video_id: int, token: UUID) -> None:
    if not MediaOperationLease.objects.filter(
        video_id=video_id,
        token=token,
        lease_type=MediaOperationLease.LEASE_ARTIFACT_WRITE,
        expires_at__gt=timezone.now(),
    ).exists():
        raise MediaOperationDeferred("Video artifact write ownership was lost.")


@contextmanager
def video_artifact_mutation(
    *,
    video_id: int,
    ttl_seconds: int = 120,
) -> Generator[None]:
    """Keep an exclusive writer barrier through storage IO, including worker loss."""
    transcode = _transcode_claim.get()
    existing = _artifact_writer.get()
    if existing is not None and existing[0] == video_id:
        _assert_artifact_writer(*existing)
        yield
        _assert_artifact_writer(*existing)
        return
    if ttl_seconds < 3:
        raise ValueError(
            "Artifact write lease lifetime must be at least three seconds."
        )
    if transcode is not None and transcode.video_id == video_id:
        # Retain a separate IO barrier even if the transcode token expires while
        # the storage backend is executing. Filesystem writes cannot be rolled back.
        with transaction.atomic():
            video = VideoFile.objects.select_for_update().get(pk=video_id)
            assert_video_transcode_lease(transcode)
            if (
                MediaOperationLease.objects.filter(
                    _blocking_leases(),
                    video_id=video_id,
                )
                .exclude(token=transcode.token)
                .exists()
            ):
                raise MediaOperationDeferred("Another video media operation is active.")
            lease = MediaOperationLease.objects.create(
                video=video,
                lease_type=MediaOperationLease.LEASE_ARTIFACT_WRITE,
                expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
                metadata={},
            )
    else:
        lease = acquire_media_operation_lease(
            request=MediaOperationLeaseAcquisition(
                video_id=video_id,
                lease_type=MediaOperationLeaseType.ARTIFACT_WRITE,
                expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
                metadata={},
            )
        )
    token = lease.token
    scope = _artifact_writer.set((video_id, token))
    stop = Event()
    failed = Event()

    def heartbeat() -> None:
        while not stop.wait(ttl_seconds / 3):
            close_old_connections()
            try:
                renew_video_artifact_write_lease(
                    video_id=video_id, token=token, ttl_seconds=ttl_seconds
                )
            except Exception as exc:
                failed.set()
                emit_structured_event(
                    logger,
                    "video_artifact_write_heartbeat_failed",
                    level=logging.ERROR,
                    video_id=video_id,
                    error_type=type(exc).__name__,
                )
                return
            finally:
                close_old_connections()

    thread = Thread(
        target=heartbeat, name=f"video-artifact-write-{video_id}", daemon=True
    )
    thread.start()
    try:
        yield
        if failed.is_set():
            raise MediaOperationDeferred("Video artifact write heartbeat failed.")
        _assert_artifact_writer(video_id, token)
        if transcode is not None and transcode.video_id == video_id:
            assert_video_transcode_lease(transcode)
    finally:
        _artifact_writer.reset(scope)
        stop.set()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError(
                "Artifact heartbeat did not stop; writer barrier retained."
            )
        with transaction.atomic():
            VideoFile.objects.select_for_update().get(pk=video_id)
            MediaOperationLease.objects.filter(token=token).delete()


@contextmanager
def video_file_save_guard(
    video: VideoFile,
    *,
    update_fields: Iterable[str] | None,
) -> Generator[None]:
    """Fence canonical pointer and timeline changes at the model boundary."""
    if not video.pk or video._state.adding:
        yield
        return
    guarded_fields = {
        "raw_file",
        "processed_file",
        "processed_video_hash",
        "video_hash",
        "fps",
        "duration",
        "frame_count",
        "meta",
    }
    if update_fields is not None:
        guarded_fields.intersection_update(update_fields)
    if not guarded_fields:
        yield
        return
    with transaction.atomic():
        previous = (
            VideoFile.objects.select_for_update()
            .filter(pk=video.pk)
            .values(*guarded_fields)
            .get()
        )
        changed = False
        for name, old_value in previous.items():
            value = getattr(video, name)
            if name.endswith("_file"):
                value = str(getattr(value, "name", "") or "")
                old_value = str(old_value or "")
            if value != old_value:
                changed = True
                break
        if changed:
            claim = _transcode_claim.get()
            writer = _artifact_writer.get()
            if claim is not None and claim.video_id == int(video.pk):
                assert_video_transcode_lease(claim)
            elif writer is not None and writer[0] == int(video.pk):
                _assert_artifact_writer(*writer)
            else:
                assert_video_not_transcoding(int(video.pk))
        yield


def expire_media_operation_leases(*, video_id: int | None = None) -> int:
    queryset = MediaOperationLease.objects.filter(
        expires_at__lte=timezone.now()
    ).exclude(lease_type=MediaOperationLease.LEASE_ARTIFACT_WRITE)
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
    return acquire_media_operation_lease(
        request=MediaOperationLeaseAcquisition(
            video_id=int(video.pk),
            lease_type=MediaOperationLeaseType.STREAM,
            expires_at=expires_at,
            metadata={"file_type": normalized_file_type},
            renew_matching=True,
        )
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
    return acquire_media_operation_lease(
        request=MediaOperationLeaseAcquisition(
            video_id=int(video.pk),
            lease_type=MediaOperationLeaseType.SEGMENT_UPDATE,
            expires_at=expires_at,
            metadata={"source": "segment_validation"},
        )
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
            _blocking_leases(),
            video_id=int(video_id),
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
    leases = MediaOperationLease.objects.filter(
        _blocking_leases(),
        video_id=int(video_id),
    )
    claim = _transcode_claim.get()
    if claim is not None and claim.video_id == int(video_id):
        assert_video_transcode_lease(claim)
        leases = leases.exclude(token=claim.token)
    writer = _artifact_writer.get()
    if writer is not None and writer[0] == int(video_id):
        _assert_artifact_writer(*writer)
        leases = leases.exclude(token=writer[1])
    return leases.exists()


def defer_if_video_media_busy(
    *,
    video_id: int,
    history: VideoProcessingHistory | None = None,
    transcode_claim: TranscodeLeaseClaim | None = None,
) -> None:
    claim = transcode_claim or _transcode_claim.get()
    if claim is not None and claim.video_id == int(video_id):
        assert_video_transcode_lease(claim)
        if (
            not MediaOperationLease.objects.filter(
                _blocking_leases(), video_id=video_id
            )
            .exclude(token=claim.token)
            .exists()
        ):
            return
    if not video_has_active_media_operation_leases(int(video_id)):
        return
    active_leases = active_media_operation_lease_summary(int(video_id))

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
