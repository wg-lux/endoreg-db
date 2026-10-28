"""Identifier-free file names for exported anonymized videos.

Exports keep the name a video was imported as, unless that name contains the
patient's first or last name. Such names are replaced by the recording start
captured before anonymization; without a recording time the anonymous raw
video hash is used. The decision is persisted when names are verified during
anonymization validation and reused by every export.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from django.db import transaction
from lx_dtypes.models.contracts.video_file import VideoFileMetaJsonObject

from endoreg_db.schemas.persisted_json import VideoFileMetaPayload
from endoreg_db.schemas.video_storage import (
    VideoExportFileName,
    VideoRecordingTimeEvidence,
)
from endoreg_db.utils.structured_logging import emit_structured_event

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)

EXPORT_VIDEO_SUFFIX = ".mp4"
RECORDING_TIME_FILE_NAME_FORMAT = "%Y-%m-%d_%H-%M-%S"
MINIMUM_NAME_PART_LENGTH = 2

_GERMAN_TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NAME_PART_SEPARATORS = re.compile(r"[\s\-_.,']+")
_UNSAFE_FILE_NAME_CHARACTERS = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")


def _comparable_variants(text: str) -> set[str]:
    """Casefolded alphanumeric forms with and without German transliteration."""
    folded = text.casefold()
    variants = {folded, folded.translate(_GERMAN_TRANSLITERATION)}
    comparable: set[str] = set()
    for variant in variants:
        decomposed = unicodedata.normalize("NFKD", variant)
        ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        comparable.add("".join(ch for ch in ascii_only if ch.isalnum()))
    return comparable


def file_name_contains_patient_name(
    file_name: str, *, first_name: str | None, last_name: str | None
) -> bool:
    """Return whether any name part occurs in the file name, ignoring case,
    accents, umlaut transliteration and separators."""
    file_variants = _comparable_variants(PurePosixPath(file_name).stem)
    for name in (first_name, last_name):
        if not name:
            continue
        for part in _NAME_PART_SEPARATORS.split(name):
            for part_variant in _comparable_variants(part):
                if len(part_variant) < MINIMUM_NAME_PART_LENGTH:
                    continue
                if any(part_variant in variant for variant in file_variants):
                    return True
    return False


def _safe_original_file_name(original_file_name: str) -> str:
    # Only the final component is meaningful; directories never leave the system.
    base_name = PurePosixPath(original_file_name.replace("\\", "/")).name
    stem, dot, _extension = base_name.rpartition(".")
    if not dot:
        stem = base_name
    stem = _UNSAFE_FILE_NAME_CHARACTERS.sub("_", stem).strip().lstrip(".")
    if not stem:
        raise ValueError("Original file name has no usable stem")
    return f"{stem}{EXPORT_VIDEO_SUFFIX}"


def _recording_time_file_name(recording_time: VideoRecordingTimeEvidence) -> str:
    started_at = recording_time.recording.recording_started_at
    if started_at is None:
        raise ValueError("Recording time evidence has no start")
    stem = datetime.fromisoformat(started_at).strftime(RECORDING_TIME_FILE_NAME_FORMAT)
    return f"{stem}{EXPORT_VIDEO_SUFFIX}"


def decide_export_file_name(
    *,
    recording_hash: str,
    original_file_name: str | None,
    first_name: str | None,
    last_name: str | None,
    recording_time: VideoRecordingTimeEvidence | None,
) -> VideoExportFileName:
    """Choose the export name; patient names never reach the result."""
    usable_recording_time = (
        recording_time
        if recording_time is not None
        and recording_time.recording_hash == recording_hash
        and recording_time.recording.recording_started_at is not None
        else None
    )
    # Legacy videos may have already purged their patient names. Without both
    # names an original filename cannot be checked and must stay local.
    if (
        first_name
        and first_name.strip()
        and last_name
        and last_name.strip()
        and original_file_name
        and not file_name_contains_patient_name(
            original_file_name, first_name=first_name, last_name=last_name
        )
    ):
        try:
            return VideoExportFileName(
                file_name=_safe_original_file_name(original_file_name),
                source="original_file_name",
                recording_hash=recording_hash,
            )
        except ValueError:
            pass  # An unusable stem is replaced exactly like an identifying one.
    if usable_recording_time is not None:
        return VideoExportFileName(
            file_name=_recording_time_file_name(usable_recording_time),
            source="recording_started_at",
            recording_hash=recording_hash,
        )
    return VideoExportFileName(
        file_name=f"{recording_hash}{EXPORT_VIDEO_SUFFIX}",
        source="raw_video_hash",
        recording_hash=recording_hash,
    )


def _decide_for_video(video: "VideoFile") -> VideoExportFileName:
    if not video.raw_video_hash:
        raise ValueError(f"Video {video.pk} has no recording identity for export")
    payload = VideoFileMetaPayload.model_validate(video.meta or {})
    sensitive_meta = video.sensitive_meta
    return decide_export_file_name(
        recording_hash=video.raw_video_hash,
        original_file_name=video.original_file_name,
        first_name=sensitive_meta.patient_first_name if sensitive_meta else None,
        last_name=sensitive_meta.patient_last_name if sensitive_meta else None,
        recording_time=payload.recording_time,
    )


def persist_validated_export_file_name(
    video: "VideoFile",
) -> VideoExportFileName | None:
    """Persist the export name once patient names have been verified.

    A video without a recording identity cannot bind a decision; validation
    proceeds and any later export of it fails loudly.
    """
    from endoreg_db.models.media.video.video_file import VideoFile

    with transaction.atomic():
        current = (
            VideoFile.objects.select_for_update(of=("self",))
            .select_related("sensitive_meta")
            .get(pk=video.pk)
        )
        if not current.raw_video_hash:
            emit_structured_event(
                logger,
                "video_export_file_name_skipped_without_recording_identity",
                level=logging.WARNING,
                video_id=video.pk,
            )
            return None
        decision = _decide_for_video(current)
        meta: VideoFileMetaJsonObject = dict(current.meta or {})
        meta["export_file_name"] = decision.model_dump(mode="json")
        current.meta = meta
        current.save(update_fields=["meta", "date_modified"])
        video.meta = meta
    emit_structured_event(
        logger,
        "video_export_file_name_decided",
        video_id=video.pk,
        source=decision.source,
    )
    return decision


def resolve_export_file_name(video: "VideoFile") -> VideoExportFileName:
    """Return the persisted decision for this recording, or derive it identically
    for videos validated before decisions were persisted."""
    payload = VideoFileMetaPayload.model_validate(video.meta or {})
    persisted = payload.export_file_name
    if persisted is not None and persisted.recording_hash == video.raw_video_hash:
        return persisted
    return _decide_for_video(video)


__all__ = [
    "decide_export_file_name",
    "file_name_contains_patient_name",
    "persist_validated_export_file_name",
    "resolve_export_file_name",
]
