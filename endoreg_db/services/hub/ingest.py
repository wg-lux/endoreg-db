from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from django.contrib.auth.models import AnonymousUser
from django.core.files import File
from django.core.exceptions import ObjectDoesNotExist

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    RawPdfFile,
    UploadJob,
    VideoFile,
)
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.utils.defaults.set_default_center import (
    get_application_defaults,
    get_default_processor,
)
from endoreg_db.utils.file_operations import sha256_file

logger = logging.getLogger(__name__)


def resolve_upload_center(
    *,
    user: Any = None,
    center_key: str | None = None,
    center_name: str | None = None,
) -> Center | None:
    if (
        user
        and not isinstance(user, AnonymousUser)
        and getattr(user, "is_authenticated", False)
    ):
        portal_user_info = getattr(user, "portaluserinfo", None)
        examiner = (
            getattr(portal_user_info, "examiner", None) if portal_user_info else None
        )
        center = getattr(examiner, "center", None) if examiner else None
        if isinstance(center, Center):
            return center

    declared_center, _ = resolve_declared_upload_center(
        center_key=center_key,
        center_name=center_name,
    )
    if declared_center is not None:
        return declared_center

    return resolve_default_center()


def resolve_declared_upload_center(
    *,
    center_key: str | None = None,
    center_name: str | None = None,
) -> tuple[Center | None, str | None]:
    normalized_center_key = (center_key or "").strip()
    normalized_center_name = (center_name or "").strip()

    if not normalized_center_key and not normalized_center_name:
        return None, None

    center_by_key = None
    center_by_name = None

    if normalized_center_key:
        center_by_key = Center.objects.filter(center_key=normalized_center_key).first()
        if center_by_key is None and not normalized_center_name:
            return None, f"Unknown center_key: {normalized_center_key}"

    if normalized_center_name:
        center_by_name = Center.objects.filter(name=normalized_center_name).first()
        if center_by_name is None and not normalized_center_key:
            return None, f"Unknown center_name: {normalized_center_name}"

    if center_by_key is not None and center_by_name is not None:
        if center_by_key.pk != center_by_name.pk:
            return None, "center_key and center_name refer to different centers"
        return center_by_key, None

    if center_by_key is not None:
        return center_by_key, None

    if center_by_name is not None:
        return center_by_name, None

    if normalized_center_key and normalized_center_name:
        return None, "Unknown center identity"

    return None, None


def resolve_default_center() -> Center | None:
    defaults = get_application_defaults()
    if defaults.center_id is not None:
        center = Center.objects.filter(pk=defaults.center_id).first()
        if center is not None:
            return center
    return Center.objects.order_by("pk").first()


def resolve_allowed_center_id(user: Any) -> int | None:
    if (
        not user
        or isinstance(user, AnonymousUser)
        or not getattr(user, "is_authenticated", False)
    ):
        return None
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return None
    portal_user_info = getattr(user, "portaluserinfo", None)
    examiner = getattr(portal_user_info, "examiner", None) if portal_user_info else None
    center_id = getattr(examiner, "center_id", None) if examiner else None
    return int(center_id) if isinstance(center_id, int) else -1


def create_or_reuse_upload_job(
    *,
    uploaded_file,
    content_type: str,
    created_by=None,
    source_center: Center | None = None,
    source_system: str = "api",
    idempotency_key: str = "",
    ingest_mode: str = UploadJob.IngestMode.API,
    processing_provenance: dict[str, Any] | None = None,
) -> tuple[UploadJob, bool]:
    upload_job_manager = cast(Any, getattr(UploadJob, "objects"))
    normalized_idempotency_key = (idempotency_key or "").strip()
    if normalized_idempotency_key:
        existing = upload_job_manager.filter(
            idempotency_key=normalized_idempotency_key,
            source_system=source_system,
            source_center=source_center,
            ingest_mode=ingest_mode,
        ).first()
        if existing is not None:
            return existing, False

    job = upload_job_manager.create(
        file=uploaded_file,
        content_type=content_type,
        source_center=source_center,
        source_system=source_system,
        idempotency_key=normalized_idempotency_key,
        ingest_mode=ingest_mode,
        original_filename=getattr(uploaded_file, "name", "") or "",
        processing_provenance=processing_provenance or {},
        created_by=created_by
        if getattr(created_by, "is_authenticated", False)
        else None,
    )
    return job, True


def create_or_reuse_watcher_upload_job(
    *,
    file_path: Path,
    content_type: str,
    source_center: Center | None = None,
    source_system: str = "watcher",
    processing_provenance: dict[str, Any] | None = None,
) -> tuple[UploadJob, bool]:
    file_hash = sha256_file(file_path)
    stat_result = file_path.stat()
    idempotency_key = (
        f"watcher:{file_hash}:{int(stat_result.st_mtime_ns)}:{stat_result.st_size}"
    )

    with file_path.open("rb") as handle:
        django_file = File(handle, name=file_path.name)
        return create_or_reuse_upload_job(
            uploaded_file=django_file,
            content_type=content_type,
            created_by=None,
            source_center=source_center,
            source_system=source_system,
            idempotency_key=idempotency_key,
            ingest_mode=UploadJob.IngestMode.WATCHER,
            processing_provenance={
                "entrypoint": "watcher",
                "watched_path": str(file_path),
                "content_hash": file_hash,
                **(processing_provenance or {}),
            },
        )


def _default_processor_name() -> str | None:
    processor = get_default_processor()
    if processor is not None:
        return processor.name
    fallback = EndoscopyProcessor.objects.order_by("pk").first()
    return fallback.name if fallback is not None else None


def process_upload_job(job_id: str) -> bool:
    upload_job_manager = cast(Any, getattr(UploadJob, "objects"))
    job = upload_job_manager.select_related("source_center", "sensitive_meta").get(
        id=job_id
    )
    if job.status == UploadJob.Status.ANONYMIZED:
        return True

    if not job.file or not job.file.name:
        job.mark_error("Upload job has no stored file")
        return False

    center = job.source_center
    if center is None:
        job.mark_error("Upload job has no resolved source center")
        return False

    job.mark_processing()
    provenance = dict(job.processing_provenance or {})
    provenance.setdefault("stored_upload_path", job.file.name)
    job.processing_provenance = provenance
    job.save(update_fields=["processing_provenance", "updated_at"])

    file_path = Path(job.file.path)
    try:
        if job.content_type == "application/pdf":
            report = ReportImportService().import_and_anonymize(
                file_path=file_path,
                center_name=center.name,
                retry=False,
                delete_source=False,
            )
            sensitive_meta = (
                report.sensitive_meta if isinstance(report, RawPdfFile) else None
            )
        else:
            processor_name = _default_processor_name()
            if not processor_name:
                raise ObjectDoesNotExist("No default EndoscopyProcessor is configured")

            video = VideoImportService().import_and_anonymize(
                file_path=file_path,
                center_name=center.name,
                processor_name=processor_name,
                retry=False,
                delete_source=False,
            )
            sensitive_meta = (
                video.sensitive_meta if isinstance(video, VideoFile) else None
            )

        job.mark_completed(sensitive_meta=sensitive_meta)
        return True
    except Exception as exc:
        logger.exception("Upload job processing failed for %s: %s", job.id, exc)
        job.mark_error(str(exc))
        return False


def process_watcher_file(
    *,
    file_path: Path | str,
    file_type: str,
    center: Center | None = None,
    processor_name: str | None = None,
    source_system: str = "watcher",
) -> UploadJob:
    watched_path = Path(file_path)
    if not watched_path.exists():
        raise FileNotFoundError(f"Watcher file not found: {watched_path}")

    source_center = center or resolve_default_center()
    if source_center is None:
        raise ObjectDoesNotExist("No center is configured for watcher ingestion")

    normalized_type = file_type.strip().lower()
    if normalized_type == "report":
        content_type = "application/pdf"
    elif normalized_type == "video":
        content_type = "video/mp4"
    else:
        raise ValueError(f"Unsupported watcher file type: {file_type}")

    upload_job, _ = create_or_reuse_watcher_upload_job(
        file_path=watched_path,
        content_type=content_type,
        source_center=source_center,
        source_system=source_system,
        processing_provenance={
            "file_type": normalized_type,
        },
    )
    if upload_job.is_complete:
        return upload_job

    upload_job.mark_processing()
    provenance = dict(upload_job.processing_provenance or {})
    provenance["watcher_processing_path"] = str(watched_path)
    upload_job.processing_provenance = provenance
    upload_job.save(update_fields=["processing_provenance", "updated_at"])

    try:
        if normalized_type == "report":
            report = ReportImportService().import_and_anonymize(
                file_path=watched_path,
                center_name=source_center.name,
                retry=False,
                delete_source=True,
            )
            sensitive_meta = (
                report.sensitive_meta if isinstance(report, RawPdfFile) else None
            )
        else:
            effective_processor_name = processor_name or _default_processor_name()
            if not effective_processor_name:
                raise ObjectDoesNotExist("No default EndoscopyProcessor is configured")
            video = VideoImportService().import_and_anonymize(
                file_path=watched_path,
                center_name=source_center.name,
                processor_name=effective_processor_name,
                retry=False,
                delete_source=True,
            )
            provenance["processor_name"] = effective_processor_name
            sensitive_meta = (
                video.sensitive_meta if isinstance(video, VideoFile) else None
            )

        upload_job.processing_provenance = provenance
        upload_job.save(update_fields=["processing_provenance", "updated_at"])
        upload_job.mark_completed(sensitive_meta=sensitive_meta)
        return upload_job
    except Exception as exc:
        logger.exception("Watcher processing failed for %s: %s", watched_path, exc)
        upload_job.mark_error(str(exc))
        raise
