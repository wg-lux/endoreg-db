from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from endoreg_db.models import Center, VideoFile
from endoreg_db.models.media.operation_lease import MediaOperationLease
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.state import video_segment_validation as segment_state
from endoreg_db.services.media_operation_gate import (
    FFMPEG_STREAM_THROTTLE_NORMAL,
    FFMPEG_STREAM_THROTTLE_STREAMING,
    MediaOperationDeferred,
    active_media_operation_lease_summary,
    create_segment_update_lease_on_commit,
    create_video_segment_update_lease,
    create_video_stream_lease,
    defer_if_video_media_busy,
    get_ffmpeg_stream_throttle_state,
    video_has_active_media_operation_leases,
    wrap_iterator_with_media_lease,
)


def _create_video() -> VideoFile:
    center = Center.objects.create(
        name=f"media-operation-gate-{uuid.uuid4().hex[:8]}",
        display_name="Media Operation Gate",
    )
    return VideoFile.objects.create(
        center=center,
        video_hash=f"media-operation-gate-{uuid.uuid4().hex}",
    )


@pytest.mark.django_db
def test_active_lease_summary_expires_stale_rows_for_video_only() -> None:
    video = _create_video()
    other_video = _create_video()
    stale_lease = MediaOperationLease.objects.create(
        video=video,
        lease_type=MediaOperationLease.LEASE_STREAM,
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    active_lease = create_video_segment_update_lease(video, ttl_seconds=30)
    other_lease = MediaOperationLease.objects.create(
        video=other_video,
        lease_type=MediaOperationLease.LEASE_STREAM,
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    summary = active_media_operation_lease_summary(video.pk)

    assert summary == [
        {
            "lease_type": MediaOperationLease.LEASE_SEGMENT_UPDATE,
            "expires_at": active_lease.expires_at,
        }
    ]
    assert not MediaOperationLease.objects.filter(pk=stale_lease.pk).exists()
    assert MediaOperationLease.objects.filter(pk=other_lease.pk).exists()
    assert video_has_active_media_operation_leases(video.pk) is True


@pytest.mark.django_db
def test_stream_iterator_releases_lease_after_consumption() -> None:
    video = _create_video()
    lease = create_video_stream_lease(video, file_type="processed", ttl_seconds=30)
    assert lease is not None

    body = b"".join(wrap_iterator_with_media_lease([b"abc", b"def"], lease))

    assert body == b"abcdef"
    assert not MediaOperationLease.objects.filter(pk=lease.pk).exists()


@pytest.mark.django_db
def test_ffmpeg_stream_throttle_state_is_normal_without_leases() -> None:
    state = get_ffmpeg_stream_throttle_state()

    assert state["mode"] == FFMPEG_STREAM_THROTTLE_NORMAL
    assert state["active_stream_leases"] == 0
    assert state["expired_leases"] == 0
    assert state["next_stream_lease_expiry"] is None


@pytest.mark.django_db
def test_ffmpeg_stream_throttle_state_is_streaming_for_active_stream_lease() -> None:
    video = _create_video()
    lease = create_video_stream_lease(video, file_type="processed", ttl_seconds=30)
    assert lease is not None

    state = get_ffmpeg_stream_throttle_state()

    assert state["mode"] == FFMPEG_STREAM_THROTTLE_STREAMING
    assert state["active_stream_leases"] == 1
    assert state["expired_leases"] == 0
    assert state["next_stream_lease_expiry"] == lease.expires_at.isoformat()


@pytest.mark.django_db
def test_ffmpeg_stream_throttle_state_expires_stale_stream_leases() -> None:
    video = _create_video()
    stale_lease = MediaOperationLease.objects.create(
        video=video,
        lease_type=MediaOperationLease.LEASE_STREAM,
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    state = get_ffmpeg_stream_throttle_state()

    assert state["mode"] == FFMPEG_STREAM_THROTTLE_NORMAL
    assert state["active_stream_leases"] == 0
    assert state["expired_leases"] == 1
    assert not MediaOperationLease.objects.filter(pk=stale_lease.pk).exists()


@pytest.mark.django_db
def test_ffmpeg_stream_throttle_state_ignores_segment_update_leases() -> None:
    video = _create_video()
    create_video_segment_update_lease(video, ttl_seconds=30)

    state = get_ffmpeg_stream_throttle_state()

    assert state["mode"] == FFMPEG_STREAM_THROTTLE_NORMAL
    assert state["active_stream_leases"] == 0


@pytest.mark.django_db
def test_defer_if_video_media_busy_marks_history_without_running_rebuild() -> None:
    video = _create_video()
    create_video_stream_lease(video, file_type="processed", ttl_seconds=30)
    history = VideoProcessingHistory.objects.create(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_PENDING,
        config=segment_state._blackening_history_config(only_validated=False),
    )

    with pytest.raises(MediaOperationDeferred, match="media operation leases"):
        defer_if_video_media_busy(video_id=video.pk, history=history)

    history.refresh_from_db()
    assert history.status == VideoProcessingHistory.STATUS_PENDING
    assert "media operation leases are active" in history.details


@pytest.mark.django_db(transaction=True)
def test_segment_update_lease_is_created_after_transaction_commit() -> None:
    video = _create_video()

    with transaction.atomic():
        create_segment_update_lease_on_commit(video)
        assert MediaOperationLease.objects.filter(video=video).count() == 0

    lease = MediaOperationLease.objects.get(video=video)
    assert lease.lease_type == MediaOperationLease.LEASE_SEGMENT_UPDATE
    assert lease.metadata == {"source": "segment_validation"}
