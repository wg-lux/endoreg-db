from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from django.db.models.fields.files import FieldFile

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.storage import field_file_is_readable, file_exists
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.storage_streaming import field_file_size


class MediaIntegrityStatus(StrEnum):
    OK = "ok"
    MISSING_CONTENT_HASH = "missing_content_hash"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    MEDIA_RECORD_MISSING = "media_record_missing"
    HASH_MISMATCH = "hash_mismatch"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_UNREADABLE = "artifact_unreadable"
    STATE_MISSING = "state_missing"
    STATE_NOT_VALIDATED = "state_not_validated"
    STATE_NOT_PROCESSED = "state_not_processed"
    METADATA_MISSING = "metadata_missing"


class MediaIntegrityExpectation(StrEnum):
    RAW_WATCHER_VIDEO = "raw_watcher_video"
    PREANONYMIZED_VIDEO = "preanonymized_video"
    REPORT = "report"


class _UploadJobCenter(Protocol):
    source_center_id: int | None


@dataclass(frozen=True)
class MediaIntegrityResult:
    ok: bool
    status: MediaIntegrityStatus
    reason: str
    content_hash: str
    media_pk: int | None = None
    missing_artifacts: tuple[str, ...] = ()


class MediaIntegrityError(RuntimeError):
    def __init__(self, result: MediaIntegrityResult) -> None:
        self.result = result
        super().__init__(
            "Media integrity check failed for "
            f"{result.content_hash or '<missing hash>'}: {result.reason}"
        )


_REPROCESSABLE_VIDEO_FAILURE_STATUSES: frozenset[MediaIntegrityStatus] = frozenset(
    {
        MediaIntegrityStatus.ARTIFACT_MISSING,
        MediaIntegrityStatus.ARTIFACT_UNREADABLE,
        MediaIntegrityStatus.STATE_MISSING,
        MediaIntegrityStatus.STATE_NOT_VALIDATED,
    }
)
_VIDEO_REPROCESSING_REQUIRED_ARTIFACTS: frozenset[str] = frozenset(
    {
        "content_hash",
        "raw_file",
        "video_file",
        "video_hash",
    }
)


def video_integrity_failure_allows_existing_video_reprocessing(
    result: MediaIntegrityResult,
) -> bool:
    """
    Return whether a reimport can repair the existing VideoFile in place.

    Processed artifacts and validation state can be rebuilt from the canonical raw
    file. An existing row with an unusable raw file requires reconciliation;
    it must never be deleted and recreated as an import fallback.
    """
    if result.ok:
        return False
    if result.status not in _REPROCESSABLE_VIDEO_FAILURE_STATUSES:
        return False
    return not _VIDEO_REPROCESSING_REQUIRED_ARTIFACTS.intersection(
        result.missing_artifacts
    )


def _ok_result(
    *,
    content_hash: str,
    media_pk: int | None,
) -> MediaIntegrityResult:
    return MediaIntegrityResult(
        ok=True,
        status=MediaIntegrityStatus.OK,
        reason="media integrity verified",
        content_hash=content_hash,
        media_pk=media_pk,
    )


def _failed_result(
    *,
    status: MediaIntegrityStatus,
    reason: str,
    content_hash: str,
    media_pk: int | None = None,
    missing_artifacts: tuple[str, ...] = (),
) -> MediaIntegrityResult:
    return MediaIntegrityResult(
        ok=False,
        status=status,
        reason=reason,
        content_hash=content_hash,
        media_pk=media_pk,
        missing_artifacts=missing_artifacts,
    )


def _required_artifacts_are_readable(
    required_artifacts: tuple[tuple[str, object], ...],
) -> tuple[MediaIntegrityStatus, tuple[str, ...]]:
    missing: list[str] = []
    unreadable: list[str] = []

    for artifact_name, field_file in required_artifacts:
        field_name = getattr(field_file, "name", None)
        if not field_file or not isinstance(field_name, str) or not field_name:
            missing.append(artifact_name)
            continue
        checked_field_file = cast(FieldFile, field_file)
        if not file_exists(checked_field_file):
            missing.append(artifact_name)
            continue
        if not field_file_is_readable(checked_field_file):
            unreadable.append(artifact_name)

    if missing:
        return MediaIntegrityStatus.ARTIFACT_MISSING, tuple(missing)
    if unreadable:
        return MediaIntegrityStatus.ARTIFACT_UNREADABLE, tuple(unreadable)
    return MediaIntegrityStatus.OK, ()


def _state_is_validated(media_obj: object) -> bool | None:
    state = getattr(media_obj, "state", None)
    if state is None:
        return None
    return bool(getattr(state, "anonymization_validated", False))


def require_reusable_video_raw_source(video: VideoFile) -> None:
    """Reject automatic reuse without discarding a potentially annotated record."""
    status, artifacts = _required_artifacts_are_readable(
        (("raw_file", video.raw_file),)
    )
    if status != MediaIntegrityStatus.OK:
        raise MediaIntegrityError(
            _failed_result(
                status=status,
                reason=(
                    "Existing video raw source is missing or unreadable; "
                    "manual reconciliation is required. Preserve the video, "
                    "annotations, published media and incoming source."
                ),
                content_hash=video.video_hash,
                media_pk=video.pk,
                missing_artifacts=artifacts,
            )
        )


def has_verified_processed_video_transfer(video: VideoFile) -> bool:
    """Recognize a received generation, never infer raw identity from output alone."""
    from endoreg_db.services.hub.transfers import get_media_envelope_receipt

    if not video.pk or not video.processed_video_hash:
        return False
    state = video.state
    if (
        state is None
        or not state.anonymized
        or not state.anonymization_validated
        or state.processed_file_sha256 != video.processed_video_hash
    ):
        return False
    transfers = TransferJob.objects.select_related(
        "source_node", "target_node", "source_center"
    ).filter(
        resource_kind=TransferJob.ResourceKind.VIDEO,
        resource_hash=video.video_hash,
        source_center_id=video.center_id,
        target_object_id=video.pk,
        transfer_mode=TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA,
        transfer_status=TransferJob.TransferStatus.APPLIED,
    )
    for transfer in transfers.iterator():
        receipt = get_media_envelope_receipt(transfer)
        if receipt is None or transfer.source_center is None:
            continue
        if (
            receipt.receiver_transfer_id != str(transfer.pk)
            or receipt.transfer_key != transfer.transfer_key
            or receipt.resource_kind != "video"
            or receipt.resource_hash != video.video_hash
            or receipt.processed_media_hash != video.processed_video_hash
            or receipt.source_center_key != transfer.source_center.center_key
            or receipt.source_node_key != transfer.source_node.node_key
            or receipt.target_node_key != transfer.target_node.node_key
        ):
            continue
        if not field_file_is_readable(video.processed_file):
            return False
        return (
            field_file_size(video.processed_file) == receipt.plaintext_size
            and sha256_file(video.processed_file) == receipt.plaintext_sha256
        )
    return False


def check_video_media_integrity(
    video: VideoFile | None,
    *,
    expectation: MediaIntegrityExpectation = MediaIntegrityExpectation.RAW_WATCHER_VIDEO,
    content_hash: str,
    require_review: bool = True,
) -> MediaIntegrityResult:
    normalized_hash = (content_hash or "").strip()
    if not normalized_hash:
        return _failed_result(
            status=MediaIntegrityStatus.MISSING_CONTENT_HASH,
            reason="Upload job does not include a content hash.",
            content_hash="",
            missing_artifacts=("content_hash",),
        )

    if video is None:
        return _failed_result(
            status=MediaIntegrityStatus.MEDIA_RECORD_MISSING,
            reason="No VideoFile exists for the expected content hash.",
            content_hash=normalized_hash,
            missing_artifacts=("video_file",),
        )

    media_pk = getattr(video, "pk", None)
    if (getattr(video, "video_hash", "") or "").strip() != normalized_hash:
        return _failed_result(
            status=MediaIntegrityStatus.HASH_MISMATCH,
            reason="VideoFile.video_hash does not match the expected content hash.",
            content_hash=normalized_hash,
            media_pk=media_pk,
            missing_artifacts=("video_hash",),
        )

    required_artifacts: list[tuple[str, object]] = []
    if expectation == MediaIntegrityExpectation.RAW_WATCHER_VIDEO and not (
        not video.raw_file and has_verified_processed_video_transfer(video)
    ):
        required_artifacts.append(("raw_file", getattr(video, "raw_file", None)))
    required_artifacts.append(
        ("processed_file", getattr(video, "processed_file", None))
    )

    artifact_status, artifacts = _required_artifacts_are_readable(
        tuple(required_artifacts)
    )
    if artifact_status != MediaIntegrityStatus.OK:
        return _failed_result(
            status=artifact_status,
            reason=f"Required video artifact(s) are not usable: {', '.join(artifacts)}.",
            content_hash=normalized_hash,
            media_pk=media_pk,
            missing_artifacts=artifacts,
        )

    validated = _state_is_validated(video)
    if validated is None:
        return _failed_result(
            status=MediaIntegrityStatus.STATE_MISSING,
            reason="VideoFile has no persisted VideoState.",
            content_hash=normalized_hash,
            media_pk=media_pk,
            missing_artifacts=("state",),
        )
    if require_review and not validated:
        return _failed_result(
            status=MediaIntegrityStatus.STATE_NOT_VALIDATED,
            reason="VideoState anonymization has not been validated.",
            content_hash=normalized_hash,
            media_pk=media_pk,
        )

    if not require_review:
        missing_metadata = tuple(
            name
            for name in ("video_meta", "sensitive_meta")
            if getattr(video, name, None) is None
        )
        if missing_metadata:
            return _failed_result(
                status=MediaIntegrityStatus.METADATA_MISSING,
                reason="Required video metadata is missing: "
                + ", ".join(missing_metadata),
                content_hash=normalized_hash,
                media_pk=media_pk,
                missing_artifacts=missing_metadata,
            )
        if not bool(getattr(video.state, "anonymized", False)):
            return _failed_result(
                status=MediaIntegrityStatus.STATE_NOT_PROCESSED,
                reason="VideoState has no persisted anonymized processing result.",
                content_hash=normalized_hash,
                media_pk=media_pk,
            )

    return _ok_result(content_hash=normalized_hash, media_pk=media_pk)


def check_report_media_integrity(
    report: RawPdfFile | None,
    *,
    content_hash: str,
) -> MediaIntegrityResult:
    normalized_hash = (content_hash or "").strip()
    if not normalized_hash:
        return _failed_result(
            status=MediaIntegrityStatus.MISSING_CONTENT_HASH,
            reason="Upload job does not include a content hash.",
            content_hash="",
            missing_artifacts=("content_hash",),
        )

    if report is None:
        return _failed_result(
            status=MediaIntegrityStatus.MEDIA_RECORD_MISSING,
            reason="No RawPdfFile exists for the expected content hash.",
            content_hash=normalized_hash,
            missing_artifacts=("raw_pdf_file",),
        )

    media_pk = getattr(report, "pk", None)
    if (getattr(report, "pdf_hash", "") or "").strip() != normalized_hash:
        return _failed_result(
            status=MediaIntegrityStatus.HASH_MISMATCH,
            reason="RawPdfFile.pdf_hash does not match the expected content hash.",
            content_hash=normalized_hash,
            media_pk=media_pk,
            missing_artifacts=("pdf_hash",),
        )

    artifact_status, artifacts = _required_artifacts_are_readable(
        (("processed_file", getattr(report, "processed_file", None)),)
    )
    if artifact_status != MediaIntegrityStatus.OK:
        return _failed_result(
            status=artifact_status,
            reason=f"Required report artifact(s) are not usable: {', '.join(artifacts)}.",
            content_hash=normalized_hash,
            media_pk=media_pk,
            missing_artifacts=artifacts,
        )

    validated = _state_is_validated(report)
    if validated is None:
        return _failed_result(
            status=MediaIntegrityStatus.STATE_MISSING,
            reason="RawPdfFile has no persisted RawPdfState.",
            content_hash=normalized_hash,
            media_pk=media_pk,
            missing_artifacts=("state",),
        )
    if not validated:
        return _failed_result(
            status=MediaIntegrityStatus.STATE_NOT_VALIDATED,
            reason="RawPdfState anonymization has not been validated.",
            content_hash=normalized_hash,
            media_pk=media_pk,
        )

    return _ok_result(content_hash=normalized_hash, media_pk=media_pk)


def _expectation_for_upload_job(
    upload_job: UploadJob,
) -> MediaIntegrityExpectation | None:
    content_type = (upload_job.content_type or "").split(";", maxsplit=1)[0].strip()
    provenance = upload_job.processing_provenance

    if content_type in {"application/pdf", "export/txt", "text/plain"}:
        return MediaIntegrityExpectation.REPORT

    if content_type.startswith("video/"):
        if (
            upload_job.storage_tier == UploadJob.StorageTier.UPLOAD_PREANONYMIZED.value
            or provenance.get("ingest_variant") == "preanonymized"
            or upload_job.source_system == "watcher_preanonymized"
        ):
            return MediaIntegrityExpectation.PREANONYMIZED_VIDEO
        return MediaIntegrityExpectation.RAW_WATCHER_VIDEO

    return None


def check_upload_job_media_integrity(
    upload_job: UploadJob, *, require_review: bool = True
) -> MediaIntegrityResult:
    content_hash = (upload_job.content_hash or "").strip()
    expectation = _expectation_for_upload_job(upload_job)
    if expectation is None:
        return _failed_result(
            status=MediaIntegrityStatus.UNSUPPORTED_CONTENT_TYPE,
            reason=f"Unsupported upload job content type: {upload_job.content_type}.",
            content_hash=content_hash,
            missing_artifacts=("content_type",),
        )

    center_id = cast(_UploadJobCenter, upload_job).source_center_id
    if center_id is None:
        return _failed_result(
            status=MediaIntegrityStatus.MEDIA_RECORD_MISSING,
            reason="Upload job has no source center for media reconciliation.",
            content_hash=content_hash,
            missing_artifacts=("source_center",),
        )

    if expectation == MediaIntegrityExpectation.REPORT:
        report = (
            RawPdfFile.objects.select_related("state")
            .filter(pdf_hash=content_hash, center_id=center_id)
            .first()
        )
        return check_report_media_integrity(report, content_hash=content_hash)

    video = (
        VideoFile.objects.select_related("state")
        .filter(video_hash=content_hash, center_id=center_id)
        .first()
    )
    return check_video_media_integrity(
        video,
        expectation=expectation,
        content_hash=content_hash,
        require_review=require_review,
    )
