"""Read-only browser exports of accepted processed media and annotations."""

from __future__ import annotations

import csv
import io
from itertools import chain
from collections.abc import Iterable, Iterator, Sequence
from time import monotonic

from django.db import transaction

from endoreg_db.config.env import get_media_operation_stream_lease_seconds
from endoreg_db.export.frames.export_frames_with_labels import (
    DEFAULT_FIELDNAMES,
    assert_video_media_export_ready,
    video_annotation_download_rows,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.video_storage import CanonicalTimelineHistoryEntry
from endoreg_db.services.media.operation_gate import create_video_stream_lease
from endoreg_db.utils.hashs import get_file_hash


def validate_video_download(video: VideoFile, *, verify_content: bool = True) -> None:
    if video.state is None:
        raise ValueError("Die Anonymisierungsfreigabe fehlt.")
    assert_video_media_export_ready(video, verify_content=False)
    if not video.processed_file:
        raise ValueError("Das anonymisierte Video ist nicht verfügbar.")
    if video.meta and video.meta.get("integrity_status") == "lost":
        raise ValueError("Das Video ist als verloren markiert.")
    actual_hash = (
        get_file_hash(video.processed_file)
        if verify_content
        else video.processed_video_hash
    )
    if not video.processed_video_hash or actual_hash != video.processed_video_hash:
        raise ValueError(
            "Die Integritätsprüfung des anonymisierten Videos ist fehlgeschlagen."
        )
    if (
        video.state.processed_file_sha256
        and video.state.processed_file_sha256 != actual_hash
    ):
        raise ValueError("Die Exportfreigabe gehört zu einer anderen Videogeneration.")


def leased_video_bytes(video: VideoFile, chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Renew access during long downloads and reject a replaced generation."""
    expected_name = video.processed_file.name
    expected_hash = video.processed_video_hash
    renewal_interval = min(10.0, get_media_operation_stream_lease_seconds() / 3)
    renewed_at = float("-inf")
    for chunk in chunks:
        if monotonic() - renewed_at >= renewal_interval:
            with transaction.atomic():
                current = VideoFile.objects.select_for_update().get(pk=video.pk)
                create_video_stream_lease(current, file_type="processed")
                if (
                    current.processed_file.name != expected_name
                    or current.processed_video_hash != expected_hash
                ):
                    raise ValueError(
                        "Die Videogeneration wurde während des Downloads ersetzt."
                    )
                validate_video_download(current, verify_content=False)
            renewed_at = monotonic()
        yield chunk


def csv_lines(rows: Iterable[Sequence[object]]) -> Iterator[str]:
    """Encode bounded rows, protecting text cells against spreadsheet formulas."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(
            [
                "'" + value
                if isinstance(value, str)
                and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                else value
                for value in row
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def annotation_csv(video: VideoFile, *, use_export_flags: bool) -> Iterator[str]:
    # Resolve and validate all coordinates before returning an HTTP success.
    rows = video_annotation_download_rows(video, use_export_flags=use_export_flags)
    return csv_lines(
        chain(
            (DEFAULT_FIELDNAMES,),
            (tuple(row[field] for field in DEFAULT_FIELDNAMES) for row in rows),
        )
    )


def timestamp_csv(video: VideoFile, *, original: bool) -> Iterator[str]:
    """Keep canonical sequence indices separate from persisted annotation frames."""
    history = (video.meta or {}).get("canonical_timeline_history")
    if not isinstance(history, list):
        raise ValueError("Keine gespeicherte kanonische Zeitstempelhistorie vorhanden.")
    entries = [CanonicalTimelineHistoryEntry.model_validate(entry) for entry in history]
    candidates = [
        entry.before if original else entry.after
        for entry in entries
        if (
            entry.before.content_hash == video.raw_video_hash
            if original
            else entry.artifact_kind == "processed"
            and entry.after.content_hash == video.processed_video_hash
        )
    ]
    if not candidates or any(candidate != candidates[0] for candidate in candidates):
        raise ValueError(
            "Die gewählte Zeitstempelfolge fehlt oder ist nicht eindeutig belegt."
        )
    timeline = candidates[0]
    fields = (
        "video_id",
        "timeline",
        "artifact_sha256",
        "frame_index",
        "presentation_timestamp",
        "stream_time_base_num",
        "stream_time_base_den",
    )
    return csv_lines(
        chain(
            (fields,),
            (
                (
                    video.pk,
                    "original" if original else "processed",
                    timeline.content_hash,
                    index,
                    timestamp,
                    timeline.time_base_num,
                    timeline.time_base_den,
                )
                for index, timestamp in enumerate(timeline.presentation_timestamps)
            ),
        )
    )
