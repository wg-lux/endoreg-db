from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from django.db import transaction
from django.dispatch import Signal
from django.db.utils import OperationalError, ProgrammingError
from django.db.models.fields.files import FieldFile

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.clinical_lifecycle import (
    ClinicalLifecycleEvent,
    ClinicalLifecycleInvariantError,
    ClinicalLifecycleTransitionError,
    ClinicalRecordKind,
)
from endoreg_db.services.clinical_lifecycle.facts import video_record_facts
from endoreg_db.services.clinical_lifecycle.persistence import (
    WorkflowStateDriftError,
    require_workflow_transition,
)
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.services.video_files.state import get_or_create_video_state
from endoreg_db.services.annotations.segment_validation_workflow import (
    resolve_segment_annotation_status,
    segment_annotations_are_final,
)
from endoreg_db.utils.file_operations import get_file_hash
from endoreg_db.utils.paths import ensure_within_protected_media_root
from lx_dtypes.models.contracts.export_ready import ReadyForExportResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VideoReadyForExportCommitted:
    """Committed approval of one processed generation for export."""

    video_id: int
    center_key: str
    processed_file_sha256: str
    # None only for an authenticated principal without a persisted user row;
    # attribution-bound receivers must refuse such events.
    approved_by_user_id: int | None


# Sent once per approval, only after the approving transaction commits, with
# ``sender=VideoFile`` and ``event=VideoReadyForExportCommitted``. Receivers
# (for example a site node staging hub transfers) must re-validate the current
# generation against ``event.processed_file_sha256`` before acting.
video_ready_for_export_committed = Signal()


class _CenterIdentity(Protocol):
    pk: int


class ReadyForExportError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _user_identifier(user: Any) -> str:
    username = str(getattr(user, "username", "") or "").strip()
    if username:
        return username
    user_id = getattr(user, "pk", None)
    if user_id is not None:
        return str(user_id)
    return "authenticated-service"


def _persisted_user_id(user: Any) -> int | None:
    user_id = getattr(user, "pk", None)
    return int(user_id) if user_id is not None else None


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
    center_pk = cast(_CenterIdentity, center).pk
    if video.center_id != center_pk:
        raise ReadyForExportError(
            "center_key does not match the video center.",
            status_code=403,
        )

    allowed_center_ids = resolve_allowed_center_ids(user)
    if allowed_center_ids == frozenset():
        raise ReadyForExportError(
            "Authenticated user is not assigned to a center.",
            status_code=403,
        )
    if allowed_center_ids is not None and center_pk not in allowed_center_ids:
        raise ReadyForExportError(
            "Video center is outside the authenticated scope.",
            status_code=403,
        )


def _processed_file(video: VideoFile) -> FieldFile:
    processed_file = getattr(video, "processed_file", None)
    if not processed_file or not getattr(processed_file, "name", None):
        raise ReadyForExportError(
            "Video has no managed processed_file artifact.",
            status_code=409,
        )
    return cast(FieldFile, processed_file)


def _verify_processed_path(processed_file: FieldFile) -> Path:
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


def _verify_clinical_lifecycle(video: VideoFile) -> None:
    """Export approval is one event of the end-to-end clinical lifecycle."""
    try:
        require_workflow_transition(
            ClinicalRecordKind.VIDEO,
            int(video.pk),
            video_record_facts(video),
            ClinicalLifecycleEvent.EXPORT_APPROVED,
        )
    except (
        ClinicalLifecycleTransitionError,
        ClinicalLifecycleInvariantError,
        WorkflowStateDriftError,
    ) as exc:
        raise ReadyForExportError(str(exc), status_code=409) from exc


def _synchronize_processed_content_hash(
    *,
    video: VideoFile,
    processed_file_sha256: str,
) -> None:
    current_hash = str(video.processed_video_hash or "").strip().lower()
    if current_hash == processed_file_sha256:
        return
    if (
        type(video)
        .objects.filter(processed_video_hash=processed_file_sha256)
        .exclude(pk=video.pk)
        .exists()
    ):
        raise ReadyForExportError(
            "The processed artifact hash is already assigned to another video.",
            status_code=409,
        )
    video.processed_video_hash = processed_file_sha256
    video.save(update_fields=["processed_video_hash", "date_modified"])


def _append_ready_audit(
    *,
    video: VideoFile,
    user: Any,
    processed_file_sha256: str,
    center_key: str,
) -> None:
    data = {
        "center_key": center_key,
        "processed_file": getattr(video.processed_file, "name", None),
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


def mark_video_ready_for_export(
    *,
    video: VideoFile,
    user: Any,
    center_key: str | None,
    expected_processed_file_sha256: str | None = None,
) -> ReadyForExportResult:
    from endoreg_db.services.video_files.timestamp_backfill import (
        prepare_video_timestamps_for_export,
    )
    from endoreg_db.utils.video.timeline import VideoTimelineMappingError

    _require_authenticated_user(user)
    video.refresh_from_db()
    center = _resolve_center(center_key)
    _verify_center_scope(user=user, video=video, center=center)
    try:
        prepare_video_timestamps_for_export(video, operator=_user_identifier(user))
    except VideoTimelineMappingError as exc:
        raise ReadyForExportError(str(exc), status_code=409) from exc
    return _mark_video_ready_for_export(
        video=video,
        user=user,
        center_key=center_key,
        expected_processed_file_sha256=expected_processed_file_sha256,
    )


@transaction.atomic
def _mark_video_ready_for_export(
    *,
    video: VideoFile,
    user: Any,
    center_key: str | None,
    expected_processed_file_sha256: str | None = None,
) -> ReadyForExportResult:
    _require_authenticated_user(user)
    video = (
        VideoFile.objects.select_for_update(of=("self",))
        .select_related("center")
        .get(pk=video.pk)
    )
    state = get_or_create_video_state(video)
    video.state = VideoState.objects.select_for_update().get(pk=state.pk)
    center = _resolve_center(center_key)
    _verify_center_scope(user=user, video=video, center=center)
    _verify_state(video)
    _verify_clinical_lifecycle(video)

    processed_file = _processed_file(video)
    _verify_processed_path(processed_file)
    processed_file_sha256 = get_file_hash(processed_file)

    expected_sha = str(expected_processed_file_sha256 or "").strip().lower()
    if expected_sha and expected_sha != processed_file_sha256:
        raise ReadyForExportError(
            "processed_file_sha256 does not match the processed artifact.",
            status_code=409,
        )

    _synchronize_processed_content_hash(
        video=video,
        processed_file_sha256=processed_file_sha256,
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
    committed_event = VideoReadyForExportCommitted(
        video_id=int(video.pk),
        center_key=str(center.center_key),
        processed_file_sha256=processed_file_sha256,
        approved_by_user_id=_persisted_user_id(user),
    )

    def _publish_committed_approval() -> None:
        video_ready_for_export_committed.send(  # pyright: ignore[reportUnknownMemberType]
            sender=VideoFile,
            event=committed_event,
        )

    transaction.on_commit(_publish_committed_approval)

    state_ready_for_export_at = cast(datetime | None, state.ready_for_export_at)
    ready_for_export_at: str | None
    if state_ready_for_export_at is not None:
        ready_for_export_at = state_ready_for_export_at.isoformat()
    else:
        ready_for_export_at = None
    return ReadyForExportResult(
        video_id=video.pk,
        ready_for_export=state.ready_for_export,
        ready_for_export_at=ready_for_export_at,
        ready_for_export_by=state.ready_for_export_by,
        processed_file_sha256=state.processed_file_sha256,
    )
