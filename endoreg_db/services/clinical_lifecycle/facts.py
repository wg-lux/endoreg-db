"""Build typed clinical lifecycle facts from persisted videos and reports.

This is the single ORM boundary of the clinical lifecycle. It only reads; it
never mutates state. Reports have no persisted medical-annotation, export
approval or dataset axis yet, so a report's annotation track never completes
and it can never be released. That is intentional and fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.sensitive_meta import SensitiveMetaState
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.annotations.segment_validation_workflow import (
    segment_annotations_are_final,
)

from .legacy_exemption import has_legacy_identity_exemption
from .state_machine import ClinicalRecordFacts, ClinicalRecordKind, ExportApproval

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.media.video.video_file import VideoFile

_INTEGRITY_LOST = "lost"


def _integrity_lost(meta: object) -> bool:
    if meta is None:
        return False
    if not isinstance(meta, Mapping):
        raise TypeError(
            f"media integrity metadata must be a mapping, got {type(meta)!r}"
        )
    status = cast(Mapping[object, object], meta).get("integrity_status", "")
    return str(status).lower() == _INTEGRITY_LOST


def _sensitive_meta(value: object) -> SensitiveMeta | None:
    if value is None or isinstance(value, SensitiveMeta):
        return value
    raise TypeError(f"expected SensitiveMeta, got {type(value)!r}")


def _identity_facts(sensitive_meta: SensitiveMeta | None) -> tuple[bool, bool]:
    """Return (identity_verified, direct_identifiers_erased)."""
    if sensitive_meta is None:
        return False, False
    try:
        verified = sensitive_meta.state_safe.is_verified
    except SensitiveMetaState.DoesNotExist:
        verified = False
    return verified, sensitive_meta.direct_identifiers_cleared_at is not None


def _video_export_approval(video: VideoFile, state: VideoState) -> ExportApproval:
    if not state.ready_for_export:
        return ExportApproval.NONE
    approved_hash = str(state.processed_file_sha256 or "").strip().lower()
    current_hash = str(video.processed_video_hash or "").strip().lower()
    if approved_hash and approved_hash == current_hash:
        return ExportApproval.CURRENT
    return ExportApproval.STALE


def video_record_facts(video: VideoFile) -> ClinicalRecordFacts:
    state_value: object = video.state
    if state_value is not None and not isinstance(state_value, VideoState):
        raise TypeError(f"expected VideoState, got {type(state_value)!r}")
    identity_verified, erased = _identity_facts(_sensitive_meta(video.sensitive_meta))
    processed_present = bool(video.processed_file and video.processed_file.name)
    raw_present = bool(video.raw_file and video.raw_file.name)
    dataset_member = AIDataSet.objects.filter(videos=video).exists()
    legacy_exempt = has_legacy_identity_exemption(int(video.pk))
    if state_value is None:
        return ClinicalRecordFacts(
            kind=ClinicalRecordKind.VIDEO,
            record_id=int(video.pk),
            integrity_lost=_integrity_lost(video.meta),
            processing_failed=False,
            processing_started=False,
            anonymized_flag=False,
            processed_artifact_present=processed_present,
            anonymization_validated=False,
            identity_verified=identity_verified,
            legacy_identity_exemption=legacy_exempt,
            raw_source_present=raw_present,
            direct_identifiers_erased=erased,
            medical_annotation_complete=False,
            export_approval=ExportApproval.NONE,
            dataset_member=dataset_member,
        )
    state = state_value
    return ClinicalRecordFacts(
        kind=ClinicalRecordKind.VIDEO,
        record_id=int(video.pk),
        integrity_lost=_integrity_lost(video.meta),
        processing_failed=bool(state.processing_error),
        processing_started=bool(state.processing_started),
        anonymized_flag=bool(state.anonymized),
        processed_artifact_present=processed_present,
        anonymization_validated=bool(state.anonymization_validated),
        identity_verified=identity_verified,
        legacy_identity_exemption=legacy_exempt,
        raw_source_present=raw_present,
        direct_identifiers_erased=erased,
        medical_annotation_complete=segment_annotations_are_final(video),
        export_approval=_video_export_approval(video, state),
        dataset_member=dataset_member,
    )


def report_record_facts(report: RawPdfFile) -> ClinicalRecordFacts:
    state: RawPdfState | None = report.state
    identity_verified, erased = _identity_facts(_sensitive_meta(report.sensitive_meta))
    processed_present = bool(report.processed_file and report.processed_file.name)
    raw_present = bool(report.file and report.file.name)
    return ClinicalRecordFacts(
        kind=ClinicalRecordKind.REPORT,
        record_id=int(report.pk),
        integrity_lost=_integrity_lost(report.raw_meta),
        processing_failed=bool(state.processing_error) if state else False,
        processing_started=bool(state.processing_started) if state else False,
        anonymized_flag=bool(state.anonymized) if state else False,
        processed_artifact_present=processed_present,
        anonymization_validated=(
            bool(state.anonymization_validated) if state else False
        ),
        identity_verified=identity_verified,
        # Legacy identity exemptions exist only for videos.
        legacy_identity_exemption=False,
        raw_source_present=raw_present,
        direct_identifiers_erased=erased,
        # No persisted report annotation, approval or dataset axis exists yet.
        medical_annotation_complete=False,
        export_approval=ExportApproval.NONE,
        dataset_member=False,
    )


__all__ = ["report_record_facts", "video_record_facts"]
