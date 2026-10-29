"""Capture the raw container recording time before anonymization strips metadata.

Masking removes all container metadata from the processed generation. The
recording start is therefore read once from the raw import, validated, and kept
out of band in ``VideoFile.meta["recording_time"]``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import transaction
from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject
from lx_dtypes.models.contracts.video_provenance import RecordingTimeRange
from pydantic import BaseModel, ConfigDict

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import VideoRecordingTimeEvidence
from endoreg_db.services.video_storage.contracts import VideoStorageNormalizationError
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.utils.structured_logging import emit_structured_event

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)

# Device clocks that were never set report MP4 epoch or 1970 defaults. Such
# values carry no recording information and are treated as absent.
MINIMUM_PLAUSIBLE_RECORDING_TIME = datetime(1990, 1, 1, tzinfo=UTC)


class _ProbeFormatTags(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    creation_time: str | None = None


class _ProbeFormat(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    tags: _ProbeFormatTags | None = None


class _ProbePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    format: _ProbeFormat | None = None


def parse_container_creation_time(raw_value: str) -> datetime | None:
    """Return an aware UTC recording start, or ``None`` for an unset device clock.

    MP4, QuickTime and Matroska store creation time in UTC, so a value without
    an explicit offset is interpreted as UTC. Unparseable values raise.
    """
    parsed = datetime.fromisoformat(raw_value.strip().replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed < MINIMUM_PLAUSIBLE_RECORDING_TIME:
        return None
    return parsed


def recording_time_evidence(
    *,
    recording_hash: str,
    recording_started_at: datetime,
    duration_seconds: float | None,
) -> VideoRecordingTimeEvidence:
    """Build typed evidence; the end is derived from the raw stream duration."""
    if recording_started_at.utcoffset() is None:
        raise ValueError("Recording start requires an explicit timezone")
    recording_ended_at = (
        (recording_started_at + timedelta(seconds=duration_seconds)).isoformat()
        if duration_seconds is not None and duration_seconds > 0
        else None
    )
    return VideoRecordingTimeEvidence(
        recording_hash=recording_hash,
        source="container_creation_time",
        recording=RecordingTimeRange(
            recording_started_at=recording_started_at.isoformat(),
            recording_ended_at=recording_ended_at,
        ),
    )


def _probe_container_creation_time(path: Path) -> str | None:
    raw_payload = ffmpeg_wrapper.get_stream_info(path)
    if raw_payload is None:
        raise VideoStorageNormalizationError(f"ffprobe failed for video: {path.name}")
    payload = _ProbePayload.model_validate(raw_payload)
    if payload.format is None or payload.format.tags is None:
        return None
    return payload.format.tags.creation_time


def persist_video_recording_time(
    video: "VideoFile", path: Path
) -> VideoRecordingTimeEvidence | None:
    """Persist the raw recording start once per recording hash.

    Missing, unset or malformed container times leave no evidence; a malformed
    value is logged and never blocks the clinical import.
    """
    from endoreg_db.models.media.video.video_file import VideoFile

    raw_creation_time = _probe_container_creation_time(path)
    if raw_creation_time is None:
        return None
    try:
        recording_started_at = parse_container_creation_time(raw_creation_time)
    except ValueError:
        emit_structured_event(
            logger,
            "video_recording_time_unparseable",
            level=logging.WARNING,
            video_id=video.pk,
        )
        return None
    if recording_started_at is None:
        emit_structured_event(
            logger,
            "video_recording_time_unset_device_clock",
            video_id=video.pk,
        )
        return None

    with transaction.atomic():
        current = VideoFile.objects.select_for_update().get(pk=video.pk)
        payload = VideoFileMetaPayload.model_validate(current.meta or {})
        existing = payload.recording_time
        if existing is not None and existing.recording_hash == current.raw_video_hash:
            video.meta = current.meta
            return existing
        source_timeline = payload.source_timeline
        evidence = recording_time_evidence(
            recording_hash=current.raw_video_hash,
            recording_started_at=recording_started_at,
            duration_seconds=(
                source_timeline.source.timeline.duration_seconds
                if source_timeline is not None
                else current.duration
            ),
        )
        meta: VideoFileMetaJsonObject = dict(current.meta or {})
        meta["recording_time"] = evidence.model_dump(mode="json")
        current.meta = meta
        current.save(update_fields=["meta", "date_modified"])
        video.meta = meta
    emit_structured_event(
        logger,
        "video_recording_time_persisted",
        video_id=video.pk,
        source=evidence.source,
    )
    return evidence


__all__ = [
    "MINIMUM_PLAUSIBLE_RECORDING_TIME",
    "parse_container_creation_time",
    "persist_video_recording_time",
    "recording_time_evidence",
]
