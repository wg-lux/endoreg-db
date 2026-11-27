"""Service-owned media evidence mutations; lifecycle derivation remains native."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, TypeVar, cast

from django.db import router, transaction
from django.utils import timezone

from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.models.state.write_guard import (
    StateWriteForbidden,
    authorize_state_write,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

MediaState = VideoState | RawPdfState
_State = TypeVar("_State", bound=MediaState)
_EXPORT_RESET: dict[str, object] = {
    "ready_for_export": False,
    "ready_for_export_at": None,
    "ready_for_export_by": "",
    "processed_file_sha256": "",
}


def _apply_media_state(
    state: _State,
    build_changes: Callable[[_State], Mapping[str, object]],
    *,
    save: bool = True,
) -> None:
    if not save:
        for name, value in build_changes(state).items():
            if name not in state.guarded_state_fields:
                raise ValueError(f"Unknown media state field: {name}")
            setattr(state, name, value)
        return
    using = state._state.db or router.db_for_write(type(state), instance=state)
    with transaction.atomic(using=using):
        current = (
            cast(
                _State,
                type(state).objects.using(using).select_for_update().get(pk=state.pk),
            )
            if state.pk is not None
            else state
        )
        expected = (
            {name: getattr(current, name) for name in current.guarded_state_fields}
            if current.pk is not None
            else None
        )
        changes = dict(build_changes(current))
        if not changes:
            return
        if unknown := changes.keys() - current.guarded_state_fields:
            raise ValueError(f"Unknown media state fields: {sorted(unknown)}")
        for name, value in changes.items():
            setattr(current, name, value)
        # These properties delegate to Rust; no Python status mirror is persisted.
        _ = current.anonymization_status
        with authorize_state_write(
            current, expected=expected, changes=changes, using=using
        ):
            current.save(
                using=using,
                update_fields=[*changes, "date_modified"]
                if expected is not None
                else None,
            )
        for name in changes:
            setattr(state, name, getattr(current, name))
        state.date_modified = current.date_modified


def update_media_state(
    state: MediaState, *, changes: Mapping[str, object], save: bool = True
) -> None:
    """Compare explicit target fields with the caller's current snapshot before writing."""
    targets = dict(changes)
    if unknown := targets.keys() - state.guarded_state_fields:
        raise ValueError(f"Unknown media state fields: {sorted(unknown)}")
    expected_fields = {name: getattr(state, name) for name in targets}

    def checked(current: MediaState) -> Mapping[str, object]:
        if current.pk is not None and any(
            getattr(current, name) != value for name, value in expected_fields.items()
        ):
            raise StateWriteForbidden("Media state changed since the caller read it")
        return targets

    _apply_media_state(state, checked, save=save)


def _set_media_state(
    state: MediaState, *, changes: Mapping[str, object], save: bool = True
) -> None:
    """Named domain operations decide from fresh state while holding its row lock."""
    _apply_media_state(state, lambda _current: changes, save=save)


def _require_video_not_failed(
    state: VideoState, changes: Mapping[str, object]
) -> Mapping[str, object]:
    if state.processing_error:
        raise ValueError(
            "Video state is marked failed/lost; cannot advance processing."
        )
    return changes


def _advance_video(
    state: VideoState, changes: Mapping[str, object], *, save: bool = True
) -> None:
    _apply_media_state(
        state, lambda current: _require_video_not_failed(current, changes), save=save
    )


def mark_video_processing_not_started(state: VideoState) -> None:
    _apply_media_state(
        state,
        lambda current: (
            {}
            if current.processing_error
            else {
                "processing_started": False,
                "anonymized": False,
                "was_created": False,
                "sensitive_meta_processed": False,
                "anonymization_validated": False,
                "outside_segments_removed": False,
                "frames_extracted": False,
                **_EXPORT_RESET,
            }
        ),
    )


def mark_video_processing_failed(state: VideoState, *, save: bool = True) -> None:
    _set_media_state(
        state,
        changes={
            "processing_error": True,
            "processing_started": False,
            **_EXPORT_RESET,
        },
        save=save,
    )


def mark_video_sensitive_meta_processed(
    state: VideoState, *, save: bool = True
) -> None:
    _advance_video(state, {"sensitive_meta_processed": True}, save=save)


def mark_video_anonymization_validated(state: VideoState, *, save: bool = True) -> None:
    _advance_video(state, {"anonymization_validated": True, **_EXPORT_RESET}, save=save)


def mark_video_outside_segments_removed(
    state: VideoState, *, save: bool = True
) -> None:
    _advance_video(
        state, {"outside_segments_removed": True, **_EXPORT_RESET}, save=save
    )


def clear_video_export_readiness(
    state: VideoState,
    *,
    clear_outside_segments_removed: bool = False,
    save: bool = True,
) -> None:
    changes = dict(_EXPORT_RESET)
    if clear_outside_segments_removed:
        changes["outside_segments_removed"] = False
    _set_media_state(state, changes=changes, save=save)


def mark_video_ready_for_export(
    state: VideoState,
    *,
    processed_file_sha256: str,
    ready_for_export_by: str,
    save: bool = True,
) -> None:
    def changes(current: VideoState) -> Mapping[str, object]:
        errors: list[str] = []
        if current.processing_error:
            errors.append("video is marked failed/lost")
        if not current.anonymization_validated:
            errors.append("anonymization has not been validated")
        if not current.outside_segments_removed:
            errors.append("outside segments have not been removed")
        if not current.segment_annotations_validated:
            errors.append("segment annotations have not been validated")
        sha = processed_file_sha256.strip().lower()
        if len(sha) != 64 or any(
            character not in "0123456789abcdef" for character in sha
        ):
            errors.append("processed_file_sha256 must be a SHA-256 hex digest")
        actor = ready_for_export_by.strip()
        if not actor:
            errors.append("ready_for_export_by is required")
        if errors:
            raise ValueError(f"Cannot mark ready for export: {'; '.join(errors)}.")
        return {
            "ready_for_export": True,
            "ready_for_export_at": timezone.now(),
            "ready_for_export_by": actor,
            "processed_file_sha256": sha,
        }

    _apply_media_state(state, changes, save=save)


def mark_video_frames_extracted(state: VideoState, *, save: bool = True) -> None:
    _advance_video(state, {"frames_extracted": True}, save=save)


def mark_video_frames_not_extracted(state: VideoState, *, save: bool = True) -> None:
    _set_media_state(state, changes={"frames_extracted": False}, save=save)


def mark_video_anonymized(state: VideoState, *, save: bool = True) -> None:
    _advance_video(
        state,
        {"anonymized": True, "outside_segments_removed": False, **_EXPORT_RESET},
        save=save,
    )


def mark_video_initial_prediction_completed(
    state: VideoState, *, save: bool = True
) -> None:
    _set_media_state(state, changes={"initial_prediction_completed": True}, save=save)


def mark_video_video_meta_extracted(state: VideoState, *, save: bool = True) -> None:
    _set_media_state(state, changes={"video_meta_extracted": True}, save=save)


def mark_video_text_meta_extracted(state: VideoState, *, save: bool = True) -> None:
    _set_media_state(state, changes={"text_meta_extracted": True}, save=save)


def mark_video_processing_started(state: VideoState, *, save: bool = True) -> None:
    _advance_video(state, {"processing_started": True}, save=save)


def mark_report_processing_not_started(state: RawPdfState) -> None:
    _set_media_state(
        state,
        changes={
            "processing_started": False,
            "anonymized": False,
            "was_created": False,
            "sensitive_meta_processed": False,
            "anonymization_validated": False,
            "processed_file_sha256": "",
        },
    )


def mark_report_processing_started(state: RawPdfState, *, save: bool = True) -> None:
    _set_media_state(
        state,
        changes={"processing_started": True, "processing_error": False},
        save=save,
    )


def mark_report_processing_failed(state: RawPdfState, *, save: bool = True) -> None:
    _set_media_state(
        state,
        changes={
            "processing_error": True,
            "processing_started": False,
            "anonymized": False,
            "sensitive_meta_processed": False,
            "anonymization_validated": False,
            "processed_file_sha256": "",
        },
        save=save,
    )


def mark_report_sensitive_meta_processed(
    state: RawPdfState, *, save: bool = True
) -> None:
    _set_media_state(state, changes={"sensitive_meta_processed": True}, save=save)


def mark_report_anonymization_validated(
    state: RawPdfState, *, save: bool = True
) -> None:
    _set_media_state(state, changes={"anonymization_validated": True}, save=save)


def mark_report_anonymized(state: RawPdfState, *, save: bool = True) -> None:
    _set_media_state(state, changes={"anonymized": True}, save=save)


def mark_report_initial_prediction_completed(
    state: RawPdfState, *, save: bool = True
) -> None:
    _set_media_state(state, changes={"initial_prediction_completed": True}, save=save)


def mark_report_text_meta_extracted(state: RawPdfState, *, save: bool = True) -> None:
    _set_media_state(state, changes={"text_meta_extracted": True}, save=save)


def _update_video_flags(video: VideoFile, changes: Mapping[str, object]) -> None:
    from endoreg_db.services.video_files.state import get_or_create_video_state

    _set_media_state(get_or_create_video_state(video), changes=changes)


def mark_frame_prediction_reset(video: VideoFile) -> None:
    _update_video_flags(
        video,
        {
            "initial_prediction_completed": False,
            "lvs_created": False,
            "frame_annotations_generated": False,
        },
    )


def mark_frame_prediction_completed(video: VideoFile) -> None:
    _update_video_flags(video, {"initial_prediction_completed": True})


def mark_prediction_segments_created(video: VideoFile, *, created: bool) -> None:
    _update_video_flags(video, {"lvs_created": created})


def mark_frame_annotations_generated(video: VideoFile) -> None:
    _update_video_flags(video, {"frame_annotations_generated": True})


def mark_segment_annotations_stale(video: VideoFile) -> None:
    _update_video_flags(
        video,
        {
            "segment_annotations_created": False,
            "segment_annotations_validated": False,
            "outside_segments_removed": False,
            **_EXPORT_RESET,
        },
    )


def mark_segment_annotations_pending_cleanup(video: VideoFile) -> None:
    _update_video_flags(
        video,
        {
            "segment_annotations_created": True,
            "segment_annotations_validated": False,
            "outside_segments_removed": False,
            **_EXPORT_RESET,
        },
    )


def mark_segment_annotations_complete_without_cleanup(video: VideoFile) -> None:
    _update_video_flags(
        video,
        {
            "segment_annotations_created": True,
            "segment_annotations_validated": True,
            "outside_segments_removed": True,
            **_EXPORT_RESET,
        },
    )


def mark_post_validation_incomplete(video: VideoFile) -> None:
    _update_video_flags(
        video,
        {
            "segment_annotations_validated": False,
            "outside_segments_removed": False,
            **_EXPORT_RESET,
        },
    )


def mark_post_validation_complete(video: VideoFile) -> None:
    _update_video_flags(
        video,
        {
            "segment_annotations_validated": True,
            "outside_segments_removed": True,
            **_EXPORT_RESET,
        },
    )


def get_state_for_video_state(state: VideoState) -> VideoState:
    from endoreg_db.services.video_files.state import get_or_create_video_state

    if not hasattr(state, "video_file"):
        raise ValueError("This method requires a related VideoFile instance.")
    return get_or_create_video_state(state.video_file)
