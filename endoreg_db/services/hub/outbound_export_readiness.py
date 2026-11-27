"""Shared read-only readiness for outbound processed-media transfers.

Lifecycle statuses come from the backend's native state services. These checks
project persisted approval and authenticated storage evidence without mutating it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cryptography.exceptions import InvalidTag
from endoreg_db import endoreg_rust_backend as native
from endoreg_db.models import RawPdfFile, VideoFile
from endoreg_db.models.state.anonymization import AnonymizationState
from endoreg_db.models.state.video_segment_validation import SegmentAnnotationStatus
from endoreg_db.services.annotations.segment_validation_workflow import (
    resolve_segment_annotation_status,
)
from endoreg_db.utils.hashs import get_file_hash


class HubExportIntegrityStatus(StrEnum):
    NOT_READY = "not_ready"
    MISSING_PROCESSED_MEDIA = "missing_processed_media"
    MISSING_HASH = "missing_hash"
    HASH_METADATA_MISMATCH = "hash_metadata_mismatch"
    PERSISTED_VERIFIED = "persisted_verified"
    VERIFIED = "verified"
    PROCESSED_MEDIA_UNREADABLE = "processed_media_unreadable"
    PROCESSED_MEDIA_HASH_MISMATCH = "processed_media_hash_mismatch"


@dataclass(frozen=True)
class VideoHubExportReadiness:
    anonymization_status: AnonymizationState
    segment_annotation_status: SegmentAnnotationStatus
    processed_media_present: bool
    export_ready_state: bool
    processed_video_hash: str
    state_processed_file_sha256: str
    actual_processed_file_sha256: str
    state_present: bool
    export_integrity_status: HubExportIntegrityStatus

    @property
    def anonymization_ready(self) -> bool:
        return self.anonymization_status == AnonymizationState.VALIDATED

    @property
    def segment_annotations_final(self) -> bool:
        return self.segment_annotation_status is SegmentAnnotationStatus.VALIDATED

    @property
    def export_integrity_ready(self) -> bool:
        return not native.hub_export_integrity_blocker(
            self.export_integrity_status.value
        )

    @property
    def transfer_eligible(self) -> bool:
        return not self.blocked_reason

    @property
    def blocked_reason(self) -> str:
        return native.video_hub_export_blocker(
            self.state_present,
            self.anonymization_ready,
            self.segment_annotation_status.value,
            self.processed_media_present,
            self.export_ready_state,
            self.export_integrity_status.value,
        )

    @property
    def transfer_validation_error(self) -> str:
        if not self.state_present:
            return "VideoFile.state must exist for outbound hub transfer."
        if (
            not self.anonymization_ready
            or not self.segment_annotations_final
            or not self.processed_media_present
            or not self.export_ready_state
        ):
            return (
                "video transfer requires validated anonymization, finalized segment "
                "annotations, and export integrity; video is not eligible."
            )
        if self.export_integrity_status is HubExportIntegrityStatus.MISSING_HASH:
            return (
                "VideoFile.processed_video_hash and VideoState.processed_file_sha256 "
                "must exist for processed-media transfer."
            )
        if (
            self.export_integrity_status
            is HubExportIntegrityStatus.HASH_METADATA_MISMATCH
        ):
            return (
                "Processed video hash metadata is inconsistent; refusing outbound "
                "transfer."
            )
        if (
            self.export_integrity_status
            is HubExportIntegrityStatus.PROCESSED_MEDIA_UNREADABLE
        ):
            return "Processed video media is unreadable; refusing outbound transfer."
        if (
            self.export_integrity_status
            is HubExportIntegrityStatus.PROCESSED_MEDIA_HASH_MISMATCH
        ):
            return (
                "Processed video file hash does not match persisted hash metadata; "
                "refusing outbound transfer."
            )
        return "video is not eligible for outbound hub transfer."


def has_usable_processed_artifact(resource: RawPdfFile | VideoFile) -> bool:
    """Fail closed unless the managed processed artifact exists and is non-empty."""
    processed_file = resource.processed_file
    stored_name = str(getattr(processed_file, "name", "") or "").strip()
    if not stored_name:
        return False
    try:
        return (
            bool(processed_file.storage.exists(stored_name))
            and int(processed_file.size) > 0
        )
    except (OSError, TypeError, ValueError):
        return False


def _authenticated_processed_digest(resource: RawPdfFile | VideoFile) -> str | None:
    try:
        return get_file_hash(resource.processed_file)
    except (InvalidTag, OSError, RuntimeError, TypeError, ValueError):
        return None


def _processed_export_integrity(
    resource: RawPdfFile | VideoFile,
    present: bool,
    approved: bool,
    digest: str,
    approval_digest: str,
    verify: bool,
) -> tuple[HubExportIntegrityStatus, str]:
    verdict = HubExportIntegrityStatus(
        native.hub_export_integrity(
            present,
            approved,
            digest,
            approval_digest,
            False,
            None,
        )
    )
    if not verify or verdict is not HubExportIntegrityStatus.PERSISTED_VERIFIED:
        return verdict, ""
    actual = _authenticated_processed_digest(resource)
    return HubExportIntegrityStatus(
        native.hub_export_integrity(
            present,
            approved,
            digest,
            approval_digest,
            True,
            actual,
        )
    ), actual or ""


def resolve_video_hub_export_state(
    video: VideoFile,
    *,
    verify_processed_media: bool = False,
) -> VideoHubExportReadiness:
    state = video.state
    if state is None:
        return VideoHubExportReadiness(
            anonymization_status=AnonymizationState.NOT_STARTED,
            segment_annotation_status=SegmentAnnotationStatus.NOT_STARTED,
            processed_media_present=False,
            export_ready_state=False,
            processed_video_hash="",
            state_processed_file_sha256="",
            actual_processed_file_sha256="",
            state_present=False,
            export_integrity_status=HubExportIntegrityStatus.NOT_READY,
        )

    processed_media_present = has_usable_processed_artifact(video)
    processed_video_hash = str(video.processed_video_hash or "").strip().lower()
    state_processed_file_sha256 = str(state.processed_file_sha256 or "").strip().lower()

    integrity_status, actual_processed_file_sha256 = _processed_export_integrity(
        video,
        processed_media_present,
        bool(state.ready_for_export),
        processed_video_hash,
        state_processed_file_sha256,
        verify_processed_media,
    )
    return VideoHubExportReadiness(
        anonymization_status=AnonymizationState(state.anonymization_status),
        segment_annotation_status=SegmentAnnotationStatus(
            resolve_segment_annotation_status(video),
        ),
        processed_media_present=processed_media_present,
        export_ready_state=bool(state.ready_for_export),
        processed_video_hash=processed_video_hash,
        state_processed_file_sha256=state_processed_file_sha256,
        actual_processed_file_sha256=actual_processed_file_sha256,
        state_present=True,
        export_integrity_status=integrity_status,
    )


def video_hub_export_blocked_reason(video: VideoFile) -> str:
    return resolve_video_hub_export_state(video).blocked_reason


def is_video_hub_export_eligible(video: VideoFile) -> bool:
    return resolve_video_hub_export_state(video).transfer_eligible


def report_hub_export_blocked_reason(
    report: RawPdfFile,
    *,
    verify_processed_media: bool = False,
) -> str:
    if report.center is None:
        return "source center missing"
    state = report.state
    if state is None or state.anonymization_status != AnonymizationState.VALIDATED:
        return "not ready for export"
    digest = state.processed_file_sha256
    if not digest or not digest.strip():
        return "processed media missing"
    verdict, _ = _processed_export_integrity(
        report,
        has_usable_processed_artifact(report),
        True,
        digest,
        digest,
        verify_processed_media,
    )
    return native.hub_export_integrity_blocker(verdict.value)


def is_report_hub_export_eligible(
    report: RawPdfFile,
    *,
    verify_processed_media: bool = False,
) -> bool:
    return (
        report_hub_export_blocked_reason(
            report,
            verify_processed_media=verify_processed_media,
        )
        == ""
    )
