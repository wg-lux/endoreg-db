from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, cast

from django.contrib.auth.models import AnonymousUser
from django.core.files import File
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from pydantic import ValidationError

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    PatientExternalID,
    RawPdfFile,
    SensitiveMeta,
    UploadJob,
    VideoFile,
)
from endoreg_db.services.auto_case_resolution import auto_resolve_media_case
from endoreg_db.services.hub.payloads import PreanonymizedIngestPayload
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.utils.defaults.set_default_center import (
    get_application_defaults,
    get_default_processor,
)
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import (
    ANONYM_REPORT_DIR,
    ANONYM_VIDEO_DIR,
    to_storage_relative,
)

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


def _load_preanonymized_sidecar(
    file_path: Path,
) -> tuple[PreanonymizedIngestPayload | None, Path | None]:
    sidecar_path = file_path.with_suffix(".json")
    if not sidecar_path.exists():
        return None, None

    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            f"Preanonymized sidecar must contain a JSON object: {sidecar_path}"
        )
    try:
        return PreanonymizedIngestPayload.model_validate(payload), sidecar_path
    except ValidationError as exc:
        raise ValueError(
            f"Invalid preanonymized sidecar payload: {sidecar_path}"
        ) from exc


def _persist_preanonymized_file(
    *,
    source_path: Path,
    target_path: Path,
    delete_source: bool,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() == target_path.resolve():
        return
    if target_path.exists():
        if delete_source:
            source_path.unlink(missing_ok=True)
        return
    if delete_source:
        shutil.move(str(source_path), str(target_path))
    else:
        shutil.copy2(source_path, target_path)


def _attach_external_id_to_sensitive_meta(
    *,
    sensitive_meta: SensitiveMeta,
    external_id: str,
    external_id_origin: str,
) -> None:
    normalized_external_id = external_id.strip()
    normalized_origin = external_id_origin.strip()
    if not normalized_external_id or not normalized_origin:
        return

    existing = PatientExternalID.objects.filter(
        origin=normalized_origin,
        external_id=normalized_external_id,
    ).first()

    if existing is None:
        pseudo_patient = sensitive_meta.pseudo_patient
        if pseudo_patient is None:
            logger.warning(
                "Skipping external_id link for SensitiveMeta %s because no pseudo patient is available yet",
                sensitive_meta.pk,
            )
            return
        existing = PatientExternalID.objects.create(
            patient=pseudo_patient,
            origin=normalized_origin,
            external_id=normalized_external_id,
        )
    elif (
        sensitive_meta.pseudo_patient_id is not None
        and existing.patient_id != sensitive_meta.pseudo_patient_id
    ):
        logger.warning(
            "external_id %s/%s already belongs to patient %s; keeping existing link instead of overwriting pseudo patient %s",
            normalized_origin,
            normalized_external_id,
            existing.patient_id,
            sensitive_meta.pseudo_patient_id,
        )

    update_fields: list[str] = []
    if sensitive_meta.external_id_id != existing.pk:
        sensitive_meta.external_id = existing
        update_fields.append("external_id")
    if sensitive_meta.pseudo_patient_id is None:
        sensitive_meta.pseudo_patient = existing.patient
        update_fields.append("pseudo_patient")
    if update_fields:
        sensitive_meta.save(update_fields=update_fields)


def _apply_preanonymized_metadata(
    *,
    sensitive_meta: SensitiveMeta | None,
    center: Center,
    payload: PreanonymizedIngestPayload | None,
) -> SensitiveMeta | None:
    if not payload:
        return sensitive_meta

    payload_copy = payload.model_dump(exclude_none=True)
    payload_copy.setdefault("center_name", center.name)

    patient_hash = payload.patient_hash or ""
    examination_hash = payload.examination_hash or ""
    external_id = payload.external_id or ""
    external_id_origin = payload.external_id_origin or ""

    if sensitive_meta is None:
        sensitive_meta = SensitiveMeta.create_from_dict(payload_copy)
    else:
        sensitive_meta = sensitive_meta.update_from_dict(payload_copy)

    if patient_hash or examination_hash:
        update_fields: list[str] = []
        if patient_hash and sensitive_meta.patient_hash != patient_hash:
            sensitive_meta.patient_hash = patient_hash
            update_fields.append("patient_hash")
        if examination_hash and sensitive_meta.examination_hash != examination_hash:
            sensitive_meta.examination_hash = examination_hash
            update_fields.append("examination_hash")
        if update_fields:
            sensitive_meta.save(update_fields=update_fields)

    if external_id and external_id_origin:
        _attach_external_id_to_sensitive_meta(
            sensitive_meta=sensitive_meta,
            external_id=external_id,
            external_id_origin=external_id_origin,
        )

    return sensitive_meta


def _finalize_preanonymized_video(
    *,
    source_path: Path,
    center: Center,
    processor_name: str | None,
    payload: PreanonymizedIngestPayload | None,
    delete_source: bool,
) -> VideoFile:
    video_hash = sha256_file(source_path)
    final_path = ANONYM_VIDEO_DIR / f"{video_hash}.mp4"
    _persist_preanonymized_file(
        source_path=source_path,
        target_path=final_path,
        delete_source=delete_source,
    )

    processor = None
    effective_processor_name = processor_name or _default_processor_name()
    if effective_processor_name:
        processor = EndoscopyProcessor.objects.filter(
            name=effective_processor_name
        ).first()

    relative_name = to_storage_relative(final_path)
    with transaction.atomic():
        video = VideoFile.objects.filter(video_hash=video_hash).first()
        if video is None:
            video = VideoFile.objects.create(
                center=center,
                processor=processor,
                original_file_name=source_path.name,
                video_hash=video_hash,
                processed_video_hash=video_hash,
                suffix=".mp4",
                processed_file=relative_name,
            )
        else:
            update_fields: list[str] = []
            if video.center_id != center.pk:
                video.center = center
                update_fields.append("center")
            if processor is not None and video.processor_id != processor.pk:
                video.processor = processor
                update_fields.append("processor")
            if video.original_file_name != source_path.name:
                video.original_file_name = source_path.name
                update_fields.append("original_file_name")
            if video.processed_video_hash != video_hash:
                video.processed_video_hash = video_hash
                update_fields.append("processed_video_hash")
            if getattr(video.processed_file, "name", None) != relative_name:
                video.processed_file.name = relative_name
                update_fields.append("processed_file")
            if update_fields:
                video.save(update_fields=update_fields)

        sensitive_meta = _apply_preanonymized_metadata(
            sensitive_meta=video.sensitive_meta,
            center=center,
            payload=payload,
        )
        update_fields = []
        if sensitive_meta is not None and video.sensitive_meta_id != sensitive_meta.pk:
            video.sensitive_meta = sensitive_meta
            update_fields.append("sensitive_meta")
        if (
            sensitive_meta is not None
            and video.patient_id != sensitive_meta.pseudo_patient_id
        ):
            video.patient = sensitive_meta.pseudo_patient
            update_fields.append("patient")
        if (
            sensitive_meta is not None
            and video.examination_id != sensitive_meta.pseudo_examination_id
        ):
            video.examination = sensitive_meta.pseudo_examination
            update_fields.append("examination")
        if update_fields:
            video.save(update_fields=update_fields)

        state = video.get_or_create_state()
        state.mark_processing_started()
        state.mark_anonymized()
        state.mark_sensitive_meta_processed()
        state.mark_anonymization_validated()

        if sensitive_meta is not None:
            try:
                auto_resolve_media_case(media_type="video", media_obj=video)
            except Exception as exc:
                logger.warning(
                    "Preanonymized video case resolution failed for %s: %s",
                    video.video_hash,
                    exc,
                )
        return video


def _finalize_preanonymized_report(
    *,
    source_path: Path,
    center: Center,
    payload: PreanonymizedIngestPayload | None,
    delete_source: bool,
) -> RawPdfFile:
    pdf_hash = sha256_file(source_path)
    final_path = ANONYM_REPORT_DIR / f"{pdf_hash}.pdf"
    _persist_preanonymized_file(
        source_path=source_path,
        target_path=final_path,
        delete_source=delete_source,
    )

    relative_name = to_storage_relative(final_path)
    with transaction.atomic():
        report = RawPdfFile.objects.filter(pdf_hash=pdf_hash).first()
        if report is None:
            report = RawPdfFile.objects.create(
                pdf_hash=pdf_hash,
                center=center,
                file=relative_name,
                processed_file=relative_name,
            )
        else:
            update_fields: list[str] = []
            if report.center_id != center.pk:
                report.center = center
                update_fields.append("center")
            if getattr(report.file, "name", None) != relative_name:
                report.file.name = relative_name
                update_fields.append("file")
            if getattr(report.processed_file, "name", None) != relative_name:
                report.processed_file.name = relative_name
                update_fields.append("processed_file")
            if update_fields:
                report.save(update_fields=update_fields)

        sensitive_meta = _apply_preanonymized_metadata(
            sensitive_meta=report.sensitive_meta,
            center=center,
            payload=payload,
        )
        update_fields = []
        if sensitive_meta is not None and report.sensitive_meta_id != sensitive_meta.pk:
            report.sensitive_meta = sensitive_meta
            update_fields.append("sensitive_meta")
        if (
            sensitive_meta is not None
            and report.patient_id != sensitive_meta.pseudo_patient_id
        ):
            report.patient = sensitive_meta.pseudo_patient
            update_fields.append("patient")
        if (
            sensitive_meta is not None
            and report.examination_id != sensitive_meta.pseudo_examination_id
        ):
            report.examination = sensitive_meta.pseudo_examination
            update_fields.append("examination")
        anonymized_text = payload.anonymized_text if payload is not None else None
        if (
            isinstance(anonymized_text, str)
            and report.anonymized_text != anonymized_text
        ):
            report.anonymized_text = anonymized_text
            update_fields.append("anonymized_text")
        if update_fields:
            report.save(update_fields=update_fields)

        state = report.get_or_create_state()
        state.mark_processing_started()
        state.mark_anonymized()
        state.mark_sensitive_meta_processed()
        state.mark_anonymization_validated()

        if sensitive_meta is not None:
            try:
                auto_resolve_media_case(media_type="pdf", media_obj=report)
            except Exception as exc:
                logger.warning(
                    "Preanonymized report case resolution failed for %s: %s",
                    report.pdf_hash,
                    exc,
                )
        return report


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


def process_preanonymized_watcher_file(
    *,
    file_path: Path | str,
    center: Center | None = None,
    processor_name: str | None = None,
    source_system: str = "watcher_preanonymized",
) -> UploadJob:
    watched_path = Path(file_path)
    if not watched_path.exists():
        raise FileNotFoundError(f"Watcher file not found: {watched_path}")

    suffix = watched_path.suffix.lower()
    if suffix == ".pdf":
        normalized_type = "report"
        content_type = "application/pdf"
    elif suffix == ".mp4":
        normalized_type = "video"
        content_type = "video/mp4"
    else:
        raise ValueError(
            f"Unsupported preanonymized watcher file suffix: {watched_path.suffix}"
        )

    source_center = center or resolve_default_center()
    if source_center is None:
        raise ObjectDoesNotExist("No center is configured for watcher ingestion")

    metadata_payload, sidecar_path = _load_preanonymized_sidecar(watched_path)
    upload_job, _ = create_or_reuse_watcher_upload_job(
        file_path=watched_path,
        content_type=content_type,
        source_center=source_center,
        source_system=source_system,
        processing_provenance={
            "file_type": normalized_type,
            "ingest_variant": "preanonymized",
            "sidecar_path": str(sidecar_path) if sidecar_path is not None else "",
            "sidecar_payload": (
                metadata_payload.model_dump(exclude_none=True)
                if metadata_payload is not None
                else {}
            ),
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
            report = _finalize_preanonymized_report(
                source_path=watched_path,
                center=source_center,
                payload=metadata_payload,
                delete_source=True,
            )
            sensitive_meta = report.sensitive_meta
        else:
            video = _finalize_preanonymized_video(
                source_path=watched_path,
                center=source_center,
                processor_name=processor_name,
                payload=metadata_payload,
                delete_source=True,
            )
            provenance["processor_name"] = processor_name or _default_processor_name()
            sensitive_meta = video.sensitive_meta

        if sidecar_path is not None and sidecar_path.exists():
            sidecar_path.unlink(missing_ok=True)
        upload_job.processing_provenance = provenance
        upload_job.save(update_fields=["processing_provenance", "updated_at"])
        upload_job.mark_completed(sensitive_meta=sensitive_meta)
        return upload_job
    except Exception as exc:
        logger.exception(
            "Preanonymized watcher processing failed for %s: %s",
            watched_path,
            exc,
        )
        upload_job.mark_error(str(exc))
        raise
