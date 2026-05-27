from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.models.state.video_segment_validation import (
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.services.hub import resolve_allowed_center_id
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.filesystem.file_operations import sha256_file
from endoreg_db.utils.filesystem.paths import ensure_within_protected_media_root

logger = logging.getLogger(__name__)


class ReadyForExportError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ReadyForExportResult:
    video_id: int
    ready_for_export: bool
    ready_for_export_at: str | None
    ready_for_export_by: str
    processed_file_sha256: str

    def to_dict(self) -> dict[str, object | None]:
        return {
            "video_id": self.video_id,
            "ready_for_export": self.ready_for_export,
            "ready_for_export_at": self.ready_for_export_at,
            "ready_for_export_by": self.ready_for_export_by,
            "processed_file_sha256": self.processed_file_sha256,
        }


def _user_identifier(user: Any) -> str:
    username = str(getattr(user, "username", "") or "").strip()
    if username:
        return username
    user_id = getattr(user, "pk", None)
    if user_id is not None:
        return str(user_id)
    return "authenticated-service"


def _require_authenticated_user(user: Any) -> None:
    if not user or not getattr(user, "is_authenticated", False):
        raise ReadyForExportError("Authentication is required.", status_code=403)


def _resolve_center(center_key: str | None) -> Center:
    normalized_center_key = str(center_key or "").strip()
    if not normalized_center_key:
        raise ReadyForExportError("center_key is required.", status_code=400)

    center = Center.objects.filter(center_key=normalized_center_key).first()
    if center is None:
        raise ReadyForExportError(
            f"Unknown center_key: {normalized_center_key}",
            status_code=400,
        )
    return center


def _verify_center_scope(*, user: Any, video: VideoFile, center: Center) -> None:
    if video.center_id != center.id:
        raise ReadyForExportError(
            "center_key does not match the video center.",
            status_code=403,
        )

    allowed_center_id = resolve_allowed_center_id(user)
    if allowed_center_id == -1:
        raise ReadyForExportError(
            "Authenticated user is not assigned to a center.",
            status_code=403,
        )
    if allowed_center_id is not None and allowed_center_id != center.id:
        raise ReadyForExportError(
            "Video center is outside the authenticated scope.",
            status_code=403,
        )


def _processed_file(video: VideoFile):
    processed_file = getattr(video, "processed_file", None)
    if not processed_file or not getattr(processed_file, "name", None):
        raise ReadyForExportError(
            "Video has no managed processed_file artifact.",
            status_code=409,
        )
    return processed_file


def _verify_processed_path(processed_file) -> Path:
    try:
        path = Path(processed_file.path).resolve(strict=True)
    except (AttributeError, NotImplementedError, OSError, ValueError) as exc:
        raise ReadyForExportError(
            "processed_file must resolve to local managed storage.",
            status_code=409,
        ) from exc

    try:
        return ensure_within_protected_media_root(path)
    except ValueError as exc:
        raise ReadyForExportError(
            "processed_file is outside protected managed storage.",
            status_code=409,
        ) from exc


def _verify_state(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
    if getattr(state, "processing_error", False):
        raise ReadyForExportError(
            "Video is marked failed/lost by media integrity.",
            status_code=409,
        )
    if not getattr(state, "anonymization_validated", False):
        raise ReadyForExportError(
            "Human anonymization validation is not complete.",
            status_code=409,
        )
    if not getattr(state, "outside_segments_removed", False):
        raise ReadyForExportError(
            "Outside segments have not been removed from the processed artifact.",
            status_code=409,
        )
    if not segment_annotations_are_final(video):
        segment_status = resolve_segment_annotation_status(video)
        raise ReadyForExportError(
            f"Segment annotation cleanup is not complete: {segment_status}.",
            status_code=409,
        )


def _append_ready_audit(
    *,
    video: VideoFile,
    user: Any,
    processed_path: Path,
    processed_file_sha256: str,
    center_key: str,
) -> None:
    data = {
        "center_key": center_key,
        "processed_file": getattr(video.processed_file, "name", None),
        "processed_file_path": str(processed_path),
        "processed_file_sha256": processed_file_sha256,
        "ready_for_export": True,
    }
    try:
        entry = AuditLedger.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            object_type="VideoFile",
            object_pk=str(video.pk),
            action="ready_for_export",
            data=data,
        )
        entry_pk = getattr(entry, "pk", None)
        if entry_pk is None or not AuditLedger.objects.filter(pk=entry_pk).exists():
            raise ReadyForExportError(
                "Audit ledger unavailable; ready-for-export promotion aborted.",
                status_code=503,
            )
    except (OperationalError, ProgrammingError) as exc:
        logger.exception("AuditLedger unavailable for ready-for-export event")
        raise ReadyForExportError(
            "Audit ledger unavailable; ready-for-export promotion aborted.",
            status_code=503,
        ) from exc


@transaction.atomic
def mark_video_ready_for_export(
    *,
    video: VideoFile,
    user: Any,
    center_key: str | None,
    expected_processed_file_sha256: str | None = None,
) -> ReadyForExportResult:
    _require_authenticated_user(user)
    center = _resolve_center(center_key)
    _verify_center_scope(user=user, video=video, center=center)
    _verify_state(video)

    processed_file = _processed_file(video)
    processed_path = _verify_processed_path(processed_file)
    processed_file_sha256 = sha256_file(processed_file)

    expected_sha = str(expected_processed_file_sha256 or "").strip().lower()
    if expected_sha and expected_sha != processed_file_sha256:
        raise ReadyForExportError(
            "processed_file_sha256 does not match the processed artifact.",
            status_code=409,
        )

    state = get_or_create_video_state(video)
    ready_by = _user_identifier(user)
    state.mark_ready_for_export(
        processed_file_sha256=processed_file_sha256,
        ready_for_export_by=ready_by,
    )
    _append_ready_audit(
        video=video,
        user=user,
        processed_path=processed_path,
        processed_file_sha256=processed_file_sha256,
        center_key=center.center_key,
    )
    emit_hub_audit_event(
        "video_ready_for_export",
        video_id=video.pk,
        center_key=center.center_key,
        processed_file_sha256=processed_file_sha256,
        request_user=user,
    )

    return ReadyForExportResult(
        video_id=video.pk,
        ready_for_export=state.ready_for_export,
        ready_for_export_at=(
            state.ready_for_export_at.isoformat()
            if state.ready_for_export_at is not None
            else None
        ),
        ready_for_export_by=state.ready_for_export_by,
        processed_file_sha256=state.processed_file_sha256,
    )
