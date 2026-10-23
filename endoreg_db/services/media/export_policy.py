"""Shared integrity and approval policy for processed-media exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q

from endoreg_db.services.hub.deployment import local_study_server_mode_enabled
from endoreg_db.utils.hashs import get_file_hash

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def validated_export_video_filter(
    *, video_prefix: str, require_export_approval: bool
) -> Q:
    """Select reviewed videos; content integrity remains an export-time check."""
    prefix = f"{video_prefix}__" if video_prefix else ""
    result = Q(**{f"{prefix}state__anonymization_validated": True})
    if require_export_approval:
        result &= Q(
            **{
                f"{prefix}state__outside_segments_removed": True,
                f"{prefix}state__ready_for_export": True,
            }
        ) & ~Q(**{f"{prefix}state__processed_file_sha256": ""})
    return result


def assert_video_media_export_ready(
    video: VideoFile, *, verify_content: bool = True
) -> str:
    """Return canonical identity; leased, previously hashed readers may skip hashing."""
    state = video.state
    if state is None:
        raise ValueError("Die Anonymisierungsfreigabe fehlt.")
    if state.processing_error:
        raise ValueError(
            f"Video {video.pk} is marked failed/lost; refusing media export"
        )
    if not state.anonymization_validated:
        raise ValueError(
            f"Video {video.pk} is not human anonymization validated; refusing media export"
        )
    local_study_server = local_study_server_mode_enabled()
    if local_study_server:
        if not state.outside_segments_removed:
            raise ValueError(
                f"Video {video.pk} has not had outside segments removed; "
                "refusing local_study_server media export"
            )
        if not state.ready_for_export:
            raise ValueError(
                f"Video {video.pk} is not marked ready_for_export; "
                "refusing local_study_server media export"
            )
    if local_study_server and len(state.processed_file_sha256.strip()) != 64:
        raise ValueError(
            f"Video {video.pk} has no processed_file_sha256 readiness proof; "
            "refusing media export"
        )
    if not video.processed_file:
        raise ValueError("Das anonymisierte Video ist nicht verfügbar.")
    if video.meta and video.meta.get("integrity_status") == "lost":
        raise ValueError("Das Video ist als verloren markiert.")
    canonical_hash = video.processed_video_hash
    actual_hash = (
        get_file_hash(video.processed_file) if verify_content else canonical_hash
    )
    if not canonical_hash or actual_hash != canonical_hash:
        raise ValueError(
            "Die Integritätsprüfung des anonymisierten Videos ist fehlgeschlagen."
        )
    if state.processed_file_sha256 and state.processed_file_sha256 != canonical_hash:
        raise ValueError("Die Exportfreigabe gehört zu einer anderen Videogeneration.")
    return canonical_hash
