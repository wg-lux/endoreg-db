from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from django.db.models.fields.files import FieldFile

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.storage import field_file_is_readable, file_exists


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


class MediaIntegrityExpectation(StrEnum):
    RAW_WATCHER_VIDEO = "raw_watcher_video"
    PREANONYMIZED_VIDEO = "preanonymized_video"
    REPORT = "report"


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
    file. If the raw file or media row is not usable, the import layer must fall
    back to its normal create-or-recreate path instead of reusing the instance.
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


def check_video_media_integrity(
    video: VideoFile | None,
    *,
    expectation: MediaIntegrityExpectation = MediaIntegrityExpectation.RAW_WATCHER_VIDEO,
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
    if expectation == MediaIntegrityExpectation.RAW_WATCHER_VIDEO:
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
    if not validated:
        return _failed_result(
            status=MediaIntegrityStatus.STATE_NOT_VALIDATED,
            reason="VideoState anonymization has not been validated.",
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
    provenance_dict = provenance if isinstance(provenance, dict) else {}

    if content_type in {"application/pdf", "export/txt", "text/plain"}:
        return MediaIntegrityExpectation.REPORT

    if content_type.startswith("video/"):
        if (
            upload_job.storage_tier == UploadJob.StorageTier.UPLOAD_PREANONYMIZED
            or provenance_dict.get("ingest_variant") == "preanonymized"
            or upload_job.source_system == "watcher_preanonymized"
        ):
            return MediaIntegrityExpectation.PREANONYMIZED_VIDEO
        return MediaIntegrityExpectation.RAW_WATCHER_VIDEO

    return None


def check_upload_job_media_integrity(upload_job: UploadJob) -> MediaIntegrityResult:
    content_hash = (upload_job.content_hash or "").strip()
    expectation = _expectation_for_upload_job(upload_job)
    if expectation is None:
        return _failed_result(
            status=MediaIntegrityStatus.UNSUPPORTED_CONTENT_TYPE,
            reason=f"Unsupported upload job content type: {upload_job.content_type}.",
            content_hash=content_hash,
            missing_artifacts=("content_type",),
        )

    if expectation == MediaIntegrityExpectation.REPORT:
        report = (
            RawPdfFile.objects.select_related("state")
            .filter(pdf_hash=content_hash)
            .first()
        )
        return check_report_media_integrity(report, content_hash=content_hash)

    video = (
        VideoFile.objects.select_related("state")
        .filter(video_hash=content_hash)
        .first()
    )
    return check_video_media_integrity(
        video,
        expectation=expectation,
        content_hash=content_hash,
    )
