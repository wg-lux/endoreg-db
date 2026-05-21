from __future__ import annotations
import uuid
import json
import logging
import hashlib
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NotRequired, Protocol, TypedDict, cast
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import AnonymousUser
from django.core.files import File
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, OperationalError, transaction
from kombu.exceptions import OperationalError as KombuOperationalError
from pydantic import ValidationError

from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    PatientExternalID,
    RawPdfFile,
    SensitiveMeta,
    UploadJob,
    VideoFile,
)
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.services.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.services.hub.cleanup import (
    cleanup_upload_job_source,
    reap_upload_job_sources,
)
from endoreg_db.services.auto_case_resolution import auto_resolve_media_case
from endoreg_db.services.hub.deployment import (
    hub_mode_enabled as _deployment_hub_mode_enabled,
    local_study_server_mode_enabled,
)
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityResult,
    check_upload_job_media_integrity,
)
from endoreg_db.services.hub.payloads import PreanonymizedIngestPayload
from endoreg_db.services.hub.payloads import LocalStudyServerPreanonymizedIngestPayload
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.services.report_llm_jobs import dispatch_report_llm_import
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.video_temporal_inference import (
    dispatch_video_temporal_inference,
)
from endoreg_db.utils.defaults.set_default_center import (
    get_application_defaults,
    get_default_processor,
)
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    ensure_directory,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.paths import to_storage_relative
from endoreg_db.utils.storage import delete_field_file, ensure_local_file


STALE_UPLOAD_JOB_AGE = timedelta(hours=2)
LOCK_RETRY_ATTEMPTS = 10
logger = logging.getLogger(__name__)
WATCHER_CLEANUP_BATCH_LIMIT = 512


class CeleryTaskDispatcher(Protocol):
    def apply_async(self, *args: Any, **kwargs: Any) -> Any:
        ...


def _video_upload_import_task_dispatcher() -> CeleryTaskDispatcher:
    # Local import avoids the task/service cycle:
    # ingest -> endoreg_db.tasks.run_video_upload_import_task ->
    # endoreg_db.services.hub.ingest._run_video_upload_import_job.
    from endoreg_db.tasks import run_video_upload_import_task

    return cast(CeleryTaskDispatcher, run_video_upload_import_task)


def _upload_job_task_dispatcher() -> CeleryTaskDispatcher:
    # Local import avoids the task/service cycle:
    # ingest -> endoreg_db.tasks.process_upload_job ->
    # endoreg_db.services.hub.process_upload_job -> ingest.
    from endoreg_db.tasks import process_upload_job as process_upload_job_task

    return cast(CeleryTaskDispatcher, process_upload_job_task)


def _is_celery_broker_connection_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, (KombuOperationalError, ConnectionRefusedError)):
            return True
        current = current.__cause__
    return False


def _processed_report_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().anonym_report


def _processed_video_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().anonym_video


def _quarantine_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().quarantine


def _opportunistic_reap_watcher_sources(
    *, limit: int = WATCHER_CLEANUP_BATCH_LIMIT
) -> int:
    try:
        return reap_upload_job_sources(limit=limit)
    except Exception as exc:
        logger.warning("Watcher source cleanup preflight failed: %s", exc)
        return 0


def _cleanup_persisted_watcher_source(upload_job: UploadJob) -> bool:
    if upload_job.ingest_mode != UploadJob.IngestMode.WATCHER:
        return False
    if upload_job.retention_policy != UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS:
        return False
    if not upload_job.source_file_persisted:
        return False

    update_fields = ["updated_at"]
    if upload_job.source_file_delete_eligible_at is None:
        upload_job.source_file_delete_eligible_at = timezone.now()
        update_fields.append("source_file_delete_eligible_at")
    if upload_job.cleanup_status != UploadJob.CleanupStatus.ELIGIBLE:
        upload_job.cleanup_status = UploadJob.CleanupStatus.ELIGIBLE
        update_fields.append("cleanup_status")
    upload_job.save(update_fields=update_fields)

    try:
        return cleanup_upload_job_source(upload_job)
    except Exception as exc:
        logger.warning(
            "Persisted watcher upload cleanup failed for %s: %s",
            upload_job.id,
            exc,
        )
        return False


class UploadProvenance(TypedDict, total=False):
    entrypoint: str
    ingest_mode: str
    source_system: str
    content_hash: str
    source_center_key: str | None
    storage_class: str
    storage_tier: str
    retention_policy: str
    hub_mode: bool
    declared_center_key: str | None
    declared_center_name: str | None
    resolved_center_key: str | None
    watched_path: str
    file_type: str
    ingest_variant: str
    sidecar_path: str
    sidecar_payload: dict[str, Any]
    watcher_processing_path: str
    processor_name: str | None
    processing_handoff: str
    llm_job_id: str
    llm_task_id: str
    llm_queue: str
    prediction_model_name: str
    prediction_task_id: str
    prediction_history_id: int | None
    prediction_queue: str
    video_import_task_id: str
    video_import_queue: str
    stored_upload_path: str
    quarantined_path: str
    quarantined_sidecar_path: str
    media_integrity_status: str
    media_integrity_reason: str
    media_integrity_missing_artifacts: list[str]
    previous_upload_job_id: str
    custom_marker: NotRequired[str]


def _upload_provenance(
    existing: UploadProvenance | None = None,
) -> UploadProvenance:
    provenance: UploadProvenance = {}
    if existing:
        provenance.update(existing)
    return provenance


def _update_upload_provenance(
    upload_job: UploadJob,
    **updates: object,
) -> UploadProvenance:
    provenance = _upload_provenance(upload_job.processing_provenance)
    for key, value in updates.items():
        if value is not None:
            cast(Any, provenance)[key] = value
    upload_job.processing_provenance = provenance
    return provenance


def record_active_learning_selection_provenance(
    upload_job: UploadJob,
    *,
    selection_strategy: str = "temporal_segment_hybrid",
    candidate_count: int,
    selected_count: int,
    annotation_budget: int,
    ai_dataset_id: int | None = None,
    model_meta_id: int | None = None,
    extra_metadata: dict[str, Any] | None = None,
    save: bool = True,
) -> UploadProvenance:
    active_learning_payload: dict[str, Any] = {
        "selection_strategy": selection_strategy,
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "annotation_budget": annotation_budget,
    }
    if ai_dataset_id is not None:
        active_learning_payload["ai_dataset_id"] = ai_dataset_id
    if model_meta_id is not None:
        active_learning_payload["model_meta_id"] = model_meta_id
    if extra_metadata:
        active_learning_payload.update(extra_metadata)

    existing_sidecar_payload = {}
    if isinstance(upload_job.processing_provenance, dict):
        current_sidecar_payload = upload_job.processing_provenance.get(
            "sidecar_payload"
        )
        if isinstance(current_sidecar_payload, dict):
            existing_sidecar_payload.update(current_sidecar_payload)
    existing_sidecar_payload["active_learning"] = active_learning_payload

    provenance = _update_upload_provenance(
        upload_job,
        ingest_variant="active_learning_selection",
        sidecar_payload=existing_sidecar_payload,
        custom_marker="active_learning",
    )
    if save:
        upload_job.save(update_fields=["processing_provenance", "updated_at"])
    return provenance


def hub_mode_enabled() -> bool:
    return _deployment_hub_mode_enabled()


def strict_center_upload_mode_enabled() -> bool:
    return hub_mode_enabled() or local_study_server_mode_enabled()


def _normalized_upload_provenance(
    *,
    ingest_mode: str,
    source_system: str,
    content_hash: str,
    source_center: Center | None,
    storage_class: str,
    storage_tier: str,
    retention_policy: str,
    processing_provenance: UploadProvenance | None = None,
) -> UploadProvenance:
    provenance = _upload_provenance(processing_provenance)
    provenance.setdefault("entrypoint", ingest_mode)
    provenance["ingest_mode"] = ingest_mode
    provenance["source_system"] = source_system
    provenance["content_hash"] = content_hash
    provenance["source_center_key"] = (
        source_center.center_key if source_center is not None else None
    )
    provenance["storage_class"] = storage_class
    provenance["storage_tier"] = storage_tier
    provenance["retention_policy"] = retention_policy
    return provenance


def _get_upload_provenance(upload_job: UploadJob) -> UploadProvenance | None:
    return cast(UploadProvenance | None, upload_job.processing_provenance)


def _compute_uploaded_file_content_hash(uploaded_file) -> str:
    digest = hashlib.sha256()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    if hasattr(uploaded_file, "chunks"):
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
    elif hasattr(uploaded_file, "read"):
        while True:
            chunk = uploaded_file.read(8192)  # Read 8KB chunks
            if not chunk:
                break
            digest.update(chunk)
    else:
        raise ValueError("uploaded_file does not have 'chunks' or 'read' method.")
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return digest.hexdigest()


def _is_retryable_db_lock_error(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "database is locked",
            "database table is locked",
            "database schema is locked",
            "database is busy",
        )
    )


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


def resolve_api_upload_context(
    *,
    user: Any = None,
    center_key: str | None = None,
    center_name: str | None = None,
) -> tuple[Center | None, int | None, str | None, dict[str, Any]]:
    normalized_center_key = (center_key or "").strip()
    normalized_center_name = (center_name or "").strip()
    hub_mode = hub_mode_enabled()
    strict_center_mode = strict_center_upload_mode_enabled()
    if strict_center_mode and not (
        user
        and not isinstance(user, AnonymousUser)
        and getattr(user, "is_authenticated", False)
    ):
        return (
            None,
            None,
            "Authentication is required for center-scoped API uploads.",
            {
                "hub_mode": hub_mode,
                "local_study_server": local_study_server_mode_enabled(),
            },
        )

    declared_center, center_resolution_error = resolve_declared_upload_center(
        center_key=normalized_center_key,
        center_name=normalized_center_name,
    )
    if center_resolution_error:
        return (
            None,
            None,
            center_resolution_error,
            {
                "hub_mode": hub_mode,
                "local_study_server": local_study_server_mode_enabled(),
            },
        )

    if strict_center_mode:
        if not normalized_center_key:
            return (
                None,
                None,
                "center_key is required for center-scoped API uploads.",
                {
                    "hub_mode": hub_mode,
                    "local_study_server": local_study_server_mode_enabled(),
                },
            )
        if declared_center is None:
            return (
                None,
                None,
                "center_key is required for center-scoped API uploads.",
                {
                    "hub_mode": hub_mode,
                    "local_study_server": local_study_server_mode_enabled(),
                },
            )

    allowed_center_id = resolve_allowed_center_id(user)
    if allowed_center_id == -1:
        return (
            None,
            allowed_center_id,
            "You do not have access to upload jobs.",
            {
                "hub_mode": hub_mode,
                "local_study_server": local_study_server_mode_enabled(),
            },
        )
    if (
        allowed_center_id is not None
        and allowed_center_id >= 0
        and declared_center is not None
        and declared_center.id != allowed_center_id
    ):
        return (
            None,
            allowed_center_id,
            "Upload center is outside the authenticated scope",
            {
                "hub_mode": hub_mode,
                "local_study_server": local_study_server_mode_enabled(),
            },
        )

    source_center = (
        declared_center
        if strict_center_mode
        else resolve_upload_center(
            user=user,
            center_key=normalized_center_key,
            center_name=normalized_center_name,
        )
    )
    emit_hub_audit_event(
        "hub.center_resolved",
        source_system="api",
        request_user=user,
        hub_mode=hub_mode,
        declared_center_key=normalized_center_key or None,
        declared_center_name=normalized_center_name or None,
        resolved_center_key=source_center.center_key if source_center else None,
        allowed_center_id=allowed_center_id,
    )
    if (
        allowed_center_id is not None
        and allowed_center_id >= 0
        and source_center is not None
        and source_center.id != allowed_center_id
    ):
        return (
            None,
            allowed_center_id,
            "Upload center is outside the authenticated scope",
            {"hub_mode": hub_mode},
        )

    return (
        source_center,
        allowed_center_id,
        None,
        {
            "hub_mode": hub_mode,
            "local_study_server": local_study_server_mode_enabled(),
            "declared_center_key": normalized_center_key or None,
            "declared_center_name": normalized_center_name or None,
            "resolved_center_key": source_center.center_key if source_center else None,
        },
    )


def _upload_job_has_usable_media(upload_job: UploadJob) -> bool:
    return check_upload_job_media_integrity(upload_job).ok


def _safe_existing_media_root_path(storage_name: str | None) -> Path | None:
    if not storage_name:
        return None
    media_root = Path(getattr(settings, "MEDIA_ROOT", "") or "")
    if not media_root:
        return None
    media_root = media_root.resolve()
    candidate = (media_root / storage_name).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


@contextmanager
def _ensure_upload_job_local_file(
    job: UploadJob,
) -> Iterator[Path]:
    try:
        with ensure_local_file(job.file) as file_path:
            yield Path(file_path)
            return
    except OSError as storage_exc:
        fallback_path = _safe_existing_media_root_path(job.file.name)
        if fallback_path is None:
            raise storage_exc
        fallback_hash = sha256_file(fallback_path)
        if job.content_hash:
            if fallback_hash != job.content_hash:
                raise IOError(
                    "Fallback upload source failed content-hash verification"
                ) from storage_exc
        else:
            job.content_hash = fallback_hash
            job.save(update_fields=["content_hash", "updated_at"])
        logger.warning(
            "Using verified MEDIA_ROOT fallback for upload job %s because storage "
            "could not materialize %s: %s",
            job.id,
            job.file.name,
            storage_exc,
        )
        yield fallback_path


def _reserve_video_upload_import_handoff(
    *,
    upload_job_id: str,
    queue: str,
    task_id: str,
) -> tuple[UploadJob, bool]:
    upload_job_manager = cast(Any, getattr(UploadJob, "objects"))
    with transaction.atomic():
        job = upload_job_manager.select_for_update().select_related(
            "source_center",
            "sensitive_meta",
        ).get(id=upload_job_id)
        if job.status == UploadJob.Status.ANONYMIZED:
            return job, False
        if not job.file or not job.file.name:
            job.mark_lost("Upload job has no stored file")
            return job, False
        if job.source_center is None:
            job.mark_error("Upload job has no resolved source center")
            return job, False

        provenance = _upload_provenance(
            cast(UploadProvenance | None, job.processing_provenance)
        )
        existing_task_id = provenance.get("video_import_task_id")
        if (
            job.status == UploadJob.Status.PROCESSING
            and isinstance(existing_task_id, str)
            and existing_task_id.strip()
        ):
            return job, False

        job.status = UploadJob.Status.PROCESSING
        job.error_detail = ""
        _update_upload_provenance(
            job,
            stored_upload_path=job.file.name,
            processing_handoff="ffmpeg_media",
            video_import_task_id=task_id,
            video_import_queue=queue,
        )
        job.save(
            update_fields=[
                "status",
                "error_detail",
                "processing_provenance",
                "updated_at",
            ]
        )
        return job, True


def _media_integrity_provenance(
    result: MediaIntegrityResult,
    *,
    previous_upload_job_id: uuid.UUID | str | None = None,
) -> dict[str, object]:
    provenance: dict[str, object] = {
        "media_integrity_status": result.status.value,
        "media_integrity_reason": result.reason,
    }
    if result.missing_artifacts:
        provenance["media_integrity_missing_artifacts"] = list(result.missing_artifacts)
    if previous_upload_job_id is not None:
        provenance["previous_upload_job_id"] = str(previous_upload_job_id)
    return provenance


def create_or_reuse_upload_job(
    *,
    uploaded_file,
    content_type: str,
    created_by=None,
    source_center: Center | None = None,
    source_system: str = "api",
    content_hash: str = "",
    idempotency_key: str = "",
    ingest_mode: str = UploadJob.IngestMode.API,
    storage_class: str = UploadJob.StorageClass.INGEST,
    storage_tier: str = UploadJob.StorageTier.UPLOAD_API,
    retention_policy: str = UploadJob.RetentionPolicy.PRESERVE_SOURCE,
    source_file_persisted: bool = True,
    cleanup_status: str = UploadJob.CleanupStatus.PENDING,
    processing_provenance: UploadProvenance | None = None,
    allow_completed_reuse_without_media: bool = False,
) -> tuple[UploadJob, bool]:
    upload_job_manager = cast(Any, getattr(UploadJob, "objects"))
    base_processing_provenance = _upload_provenance(processing_provenance)
    reingest_provenance_updates: dict[str, object] = {}
    normalized_content_hash = (content_hash or "").strip()
    if not normalized_content_hash:
        normalized_content_hash = _compute_uploaded_file_content_hash(uploaded_file)

    normalized_idempotency_key = (idempotency_key or "").strip()

    def _matching_active_job() -> UploadJob | None:
        existing_job_qs = (
            upload_job_manager.filter(
                source_center=source_center,
                content_type=content_type,
            )
            .exclude(status__in=[UploadJob.Status.ERROR, UploadJob.Status.LOST])
            .select_for_update()
        )

        existing_job = None
        if normalized_content_hash:
            existing_job = existing_job_qs.filter(
                content_hash=normalized_content_hash
            ).first()
        if existing_job is None and normalized_idempotency_key:
            existing_job = existing_job_qs.filter(
                idempotency_key=normalized_idempotency_key,
                source_system=source_system,
                ingest_mode=ingest_mode,
                storage_class=storage_class,
                storage_tier=storage_tier,
            ).first()
        return existing_job

    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            invalid_job_id: uuid.UUID | None = None
            invalid_reason: str | None = None
            invalid_status: str = UploadJob.Status.ERROR
            invalid_integrity_result: MediaIntegrityResult | None = None
            with transaction.atomic():
                existing_job = _matching_active_job()
                if existing_job is not None:
                    is_valid_reuse = False

                    if existing_job.status in [
                        UploadJob.Status.PENDING,
                        UploadJob.Status.PROCESSING,
                    ]:
                        updated_at = getattr(existing_job, "updated_at", None)
                        if (
                            updated_at
                            and timezone.now() - updated_at <= STALE_UPLOAD_JOB_AGE
                        ):
                            is_valid_reuse = True
                        else:
                            invalid_reason = (
                                "Existing upload job was stale in pending/processing "
                                "state. Forcing re-ingest."
                            )
                    elif existing_job.status == UploadJob.Status.ANONYMIZED:
                        integrity_result = check_upload_job_media_integrity(
                            existing_job
                        )
                        if integrity_result.ok:
                            is_valid_reuse = True
                        else:
                            invalid_integrity_result = integrity_result
                            invalid_reason = (
                                "Completed upload job failed media integrity check: "
                                f"{integrity_result.reason} Forcing re-ingest."
                            )
                            invalid_status = UploadJob.Status.LOST
                    else:
                        invalid_reason = (
                            "Previous job was incomplete or invalid for reuse. "
                            "Forcing re-ingest."
                        )

                    if is_valid_reuse:
                        emit_hub_audit_event(
                            "hub.upload_job_reused",
                            upload_job_id=str(existing_job.id),
                            source_system=source_system,
                            request_user=created_by,
                            center_key=(
                                source_center.center_key if source_center else None
                            ),
                            ingest_mode=ingest_mode,
                            idempotency_key=normalized_idempotency_key,
                        )
                        return existing_job, False

                    logger.warning(
                        "UploadJob %s found but not valid for reuse (status: %s). %s",
                        existing_job.id,
                        existing_job.status,
                        invalid_reason,
                    )
                    invalid_job_id = existing_job.id
                    invalid_reason = (
                        invalid_reason
                        or "Previous upload job was invalid for reuse. Forcing re-ingest."
                    )
                else:
                    try:
                        if hasattr(uploaded_file, "seek"):
                            uploaded_file.seek(0)
                        job = upload_job_manager.create(
                            file=uploaded_file,
                            content_type=content_type,
                            source_center=source_center,
                            source_system=source_system,
                            content_hash=normalized_content_hash,
                            idempotency_key=normalized_idempotency_key,
                            ingest_mode=ingest_mode,
                            storage_class=storage_class,
                            storage_tier=storage_tier,
                            retention_policy=retention_policy,
                            source_file_persisted=source_file_persisted,
                            cleanup_status=cleanup_status,
                            original_filename=getattr(uploaded_file, "name", "") or "",
                            processing_provenance=_normalized_upload_provenance(
                                ingest_mode=ingest_mode,
                                source_system=source_system,
                                content_hash=normalized_content_hash,
                                source_center=source_center,
                                storage_class=storage_class,
                                storage_tier=storage_tier,
                                retention_policy=retention_policy,
                                processing_provenance=cast(
                                    UploadProvenance,
                                    {
                                        **base_processing_provenance,
                                        **reingest_provenance_updates,
                                    },
                                ),
                            ),
                            created_by=(
                                created_by
                                if getattr(created_by, "is_authenticated", False)
                                else None
                            ),
                        )
                        emit_hub_audit_event(
                            "hub.upload_job_created",
                            upload_job_id=str(job.id),
                            source_system=source_system,
                            request_user=created_by,
                            center_key=(
                                source_center.center_key if source_center else None
                            ),
                            ingest_mode=ingest_mode,
                            content_hash=normalized_content_hash,
                            idempotency_key=normalized_idempotency_key,
                            storage_tier=storage_tier,
                            retention_policy=retention_policy,
                        )
                        return job, True
                    except IntegrityError:
                        conflict_job = _matching_active_job()
                        if conflict_job is not None:
                            return conflict_job, False
                        raise

            if invalid_job_id is not None:
                invalid_job = (
                    UploadJob.objects.filter(pk=invalid_job_id)
                    .exclude(status__in=[UploadJob.Status.ERROR, UploadJob.Status.LOST])
                    .first()
                )
                if invalid_job is not None:
                    if invalid_integrity_result is not None:
                        provenance_updates = _media_integrity_provenance(
                            invalid_integrity_result
                        )
                        _update_upload_provenance(invalid_job, **provenance_updates)
                        invalid_job.save(
                            update_fields=["processing_provenance", "updated_at"]
                        )
                        emit_hub_audit_event(
                            "hub.upload_job_media_integrity_failed",
                            upload_job_id=str(invalid_job.id),
                            source_system=invalid_job.source_system,
                            request_user=created_by,
                            center_key=(
                                invalid_job.source_center.center_key
                                if invalid_job.source_center
                                else None
                            ),
                            ingest_mode=invalid_job.ingest_mode,
                            content_hash=invalid_job.content_hash,
                            media_integrity_status=(
                                invalid_integrity_result.status.value
                            ),
                            media_integrity_reason=invalid_integrity_result.reason,
                            missing_artifacts=list(
                                invalid_integrity_result.missing_artifacts
                            ),
                        )
                        reingest_provenance_updates = _media_integrity_provenance(
                            invalid_integrity_result,
                            previous_upload_job_id=invalid_job.id,
                        )
                    if invalid_status == UploadJob.Status.LOST:
                        invalid_job.mark_lost(invalid_reason)
                    else:
                        invalid_job.mark_error(invalid_reason)
                    _cleanup_persisted_watcher_source(invalid_job)
                continue
        except OperationalError as exc:
            if not _is_retryable_db_lock_error(exc) or attempt == LOCK_RETRY_ATTEMPTS:
                raise
            logger.warning(
                "UploadJob create/reuse hit a locked database for source_system=%s "
                "idempotency_key=%s attempt=%d/%d; retrying.",
                source_system,
                normalized_idempotency_key,
                attempt,
                LOCK_RETRY_ATTEMPTS,
            )
            time.sleep(0.1 * attempt)
    raise RuntimeError("UploadJob create/reuse exhausted lock retries")


def create_or_reuse_watcher_upload_job(
    *,
    file_path: Path,
    content_type: str,
    source_center: Center | None = None,
    source_system: str = "watcher",
    storage_class: str = UploadJob.StorageClass.INGEST,
    storage_tier: str = UploadJob.StorageTier.UPLOAD_WATCHER,
    retention_policy: str = UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
    processing_provenance: UploadProvenance | None = None,
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
            content_hash=file_hash,
            idempotency_key=idempotency_key,
            ingest_mode=UploadJob.IngestMode.WATCHER,
            storage_class=storage_class,
            storage_tier=storage_tier,
            retention_policy=retention_policy,
            source_file_persisted=True,
            cleanup_status=UploadJob.CleanupStatus.PENDING,
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
    *,
    strict: bool = False,
) -> tuple[PreanonymizedIngestPayload | None, Path | None]:
    sidecar_path = file_path.with_suffix(".json")
    if not sidecar_path.exists():
        if strict:
            raise ValueError(f"Preanonymized sidecar is required: {sidecar_path}")
        return None, None

    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            f"Preanonymized sidecar must contain a JSON object: {sidecar_path}"
        )
    try:
        model_cls = (
            LocalStudyServerPreanonymizedIngestPayload
            if strict
            else PreanonymizedIngestPayload
        )
        return model_cls.model_validate(payload), sidecar_path
    except ValidationError as exc:
        raise ValueError(
            f"Invalid preanonymized sidecar payload: {sidecar_path}"
        ) from exc


def _quarantine_preanonymized_drop(
    *,
    media_path: Path,
    sidecar_path: Path | None,
    upload_job: UploadJob | None = None,
) -> None:
    updates: dict[str, str] = {}
    quarantine_dir = _quarantine_dir()
    if media_path.exists():
        quarantine_path = quarantine_dir / media_path.name
        if quarantine_path.exists():
            quarantine_path = quarantine_dir / f"{uuid.uuid4().hex}_{media_path.name}"
        atomic_move_file(source=media_path, destination=quarantine_path)
        updates["quarantined_path"] = str(quarantine_path)
    if sidecar_path is not None and sidecar_path.exists():
        quarantine_sidecar_path = quarantine_dir / sidecar_path.name
        if quarantine_sidecar_path.exists():
            quarantine_sidecar_path = (
                quarantine_dir / f"{uuid.uuid4().hex}_{sidecar_path.name}"
            )
        atomic_move_file(source=sidecar_path, destination=quarantine_sidecar_path)
        updates["quarantined_sidecar_path"] = str(quarantine_sidecar_path)
    if upload_job is not None and updates:
        _update_upload_provenance(upload_job, **updates)
        upload_job.save(update_fields=["processing_provenance", "updated_at"])


def _quarantine_upload_job_file(
    upload_job: UploadJob,
    *,
    local_path: Path,
) -> Path | None:
    if not local_path.exists() or not local_path.is_file():
        return None

    quarantine_dir = _quarantine_dir()
    ensure_directory(quarantine_dir)
    original_name = (
        upload_job.original_filename
        or Path(getattr(upload_job.file, "name", "")).name
        or local_path.name
    )
    quarantine_path = quarantine_dir / original_name
    if quarantine_path.exists():
        quarantine_path = quarantine_dir / f"{uuid.uuid4().hex}_{original_name}"

    atomic_copy_file(source=local_path, destination=quarantine_path)
    delete_field_file(upload_job, "file", missing_ok=True, save=False)
    safe_unlink_file(local_path, missing_ok=True)
    upload_job.file.name = ""
    upload_job.source_file_persisted = False
    upload_job.cleanup_status = UploadJob.CleanupStatus.COMPLETED
    _update_upload_provenance(upload_job, quarantined_path=str(quarantine_path))
    upload_job.save(
        update_fields=[
            "file",
            "source_file_persisted",
            "cleanup_status",
            "processing_provenance",
            "updated_at",
        ]
    )
    return quarantine_path


def _validate_local_preanonymized_drop_path(watched_path: Path) -> None:
    drop_root = path_utils.WATCHER_PREANONYMIZED_DROP_DIR.resolve()
    try:
        watched_path.resolve().relative_to(drop_root)
    except ValueError as exc:
        raise ValueError(
            f"Preanonymized watcher file must remain inside {drop_root}"
        ) from exc


def _persist_preanonymized_file(
    *,
    source_path: Path,
    target_path: Path,
    delete_source: bool,
) -> None:
    ensure_directory(target_path.parent)
    if source_path.resolve() == target_path.resolve():
        return
    if target_path.exists():
        if delete_source:
            safe_unlink_file(source_path, missing_ok=True)
        return
    if delete_source:
        atomic_move_file(source=source_path, destination=target_path)
    else:
        atomic_copy_file(source=source_path, destination=target_path)


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

    update_fields: list[str] = []
    if sensitive_meta.external_id_id != existing.pk:
        sensitive_meta.external_id = existing
        update_fields.append("external_id")
    if sensitive_meta.pseudo_patient_id is None:
        sensitive_meta.pseudo_patient = existing.patient
        update_fields.append("pseudo_patient")
    if update_fields:
        sensitive_meta.save(update_fields=update_fields)


def _normalize_sensitive_meta_payload(
    *,
    payload: dict[str, Any],
    center: Center,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {"center_name": center.name}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            normalized[key] = stripped
            continue
        normalized[key] = value

    try:
        from lx_dtypes.models.meta.SensitiveMeta import (
            SensitiveMeta as LxSensitiveMetaContract,
        )
    except Exception as exc:
        logger.debug("lx_dtypes SensitiveMeta unavailable for normalization: %s", exc)
        return normalized

    contract_input: dict[str, Any] = {}
    key_map = {
        "patient_first_name": "first_name",
        "patient_last_name": "last_name",
        "patient_dob": "dob",
        "patient_gender": "gender",
        "examination_date": "examination_date",
        "examination_time": "examination_time",
        "casenumber": "casenumber",
        "text": "text",
        "anonymized_text": "anonymized_text",
        "endoscope_type": "endoscope_type",
        "endoscope_sn": "endoscope_sn",
        "external_id": "external_id",
    }
    for source_key, target_key in key_map.items():
        value = normalized.get(source_key)
        if value is None:
            continue
        if source_key == "patient_gender" and hasattr(value, "name"):
            value = getattr(value, "name")
        contract_input[target_key] = value

    if not contract_input:
        return normalized

    validated = LxSensitiveMetaContract.model_validate(contract_input)
    contract_dump = validated.model_dump(
        include={
            "first_name",
            "last_name",
            "dob",
            "gender",
            "examination_date",
            "examination_time",
            "casenumber",
            "text",
            "anonymized_text",
            "endoscope_type",
            "endoscope_sn",
            "external_id",
        },
        exclude_none=True,
    )

    reverse_key_map = {
        "first_name": "patient_first_name",
        "last_name": "patient_last_name",
        "dob": "patient_dob",
        "gender": "patient_gender",
        "examination_date": "examination_date",
        "examination_time": "examination_time",
        "casenumber": "casenumber",
        "text": "text",
        "anonymized_text": "anonymized_text",
        "endoscope_type": "endoscope_type",
        "endoscope_sn": "endoscope_sn",
        "external_id": "external_id",
    }
    for source_key, target_key in reverse_key_map.items():
        value = contract_dump.get(source_key)
        if value is not None:
            normalized[target_key] = value

    return normalized


def _apply_preanonymized_metadata(
    *,
    sensitive_meta: SensitiveMeta | None,
    center: Center,
    payload: PreanonymizedIngestPayload | None,
) -> SensitiveMeta | None:
    if not payload:
        return sensitive_meta

    payload_copy = _normalize_sensitive_meta_payload(
        payload=payload.model_dump(exclude_none=True),
        center=center,
    )

    patient_hash = payload.patient_hash or ""
    examination_hash = payload.examination_hash or ""
    external_id = payload.external_id or ""
    external_id_origin = payload.external_id_origin or ""
    payload_copy.pop("external_id", None)
    payload_copy.pop("external_id_origin", None)

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
    final_path = _processed_video_dir() / f"{video_hash}.mp4"
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
        try:
            sync_video_streamable_artifacts(
                video,
                include_raw=True,
                include_processed=True,
                save=True,
            )
        except Exception as exc:
            logger.warning(
                "Could not synchronize streamable artifacts for preanonymized video %s: %s",
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
    final_path = _processed_report_dir() / f"{pdf_hash}.pdf"
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
        job.mark_lost("Upload job has no stored file")
        return False

    center = job.source_center
    if center is None:
        job.mark_error("Upload job has no resolved source center")
        return False

    if job.content_type == "application/pdf":
        job.mark_processing()
        provenance = _update_upload_provenance(job, stored_upload_path=job.file.name)
        provenance.setdefault("stored_upload_path", job.file.name)
        job.save(update_fields=["processing_provenance", "updated_at"])

        dispatch_result = dispatch_report_llm_import(
            upload_job_id=str(job.id),
            payload={"source": "upload_job"},
        )
        _update_upload_provenance(
            job,
            processing_handoff="llm_inference",
            llm_job_id=dispatch_result.job_id,
            llm_task_id=dispatch_result.task_id,
            llm_queue=dispatch_result.queue,
        )
        job.save(update_fields=["processing_provenance", "updated_at"])
        if dispatch_result.status in {"queued", "already_queued", "completed"}:
            return True
        if dispatch_result.status == "lost":
            job.mark_lost(dispatch_result.reason or "Report LLM source is missing")
            return False
        job.mark_error(dispatch_result.reason or "Report LLM dispatch failed")
        return False

    try:
        queue = queue_for_job_kind(HeavyJobKind.VIDEO_UPLOAD_IMPORT)
        task_id = uuid.uuid4().hex
        reserved_job, should_dispatch = _reserve_video_upload_import_handoff(
            upload_job_id=str(job.id),
            queue=queue,
            task_id=task_id,
        )
        if not should_dispatch:
            return reserved_job.status in {
                UploadJob.Status.PROCESSING,
                UploadJob.Status.ANONYMIZED,
            }
        ensure_secure_transport_for_job_kind(HeavyJobKind.VIDEO_UPLOAD_IMPORT)
        async_result = _video_upload_import_task_dispatcher().apply_async(
            args=(str(job.id),),
            queue=queue,
            routing_key=queue,
            task_id=task_id,
        )
        if str(async_result.id) != task_id:
            _update_upload_provenance(
                reserved_job,
                video_import_task_id=str(async_result.id),
            )
            reserved_job.save(update_fields=["processing_provenance", "updated_at"])
        return True
    except Exception as exc:
        logger.exception("Video upload import handoff failed for %s: %s", job_id, exc)
        job.mark_error(f"Failed to start video import: {exc}")
        return False


def _run_video_upload_import_job(job_id: str) -> bool:
    upload_job_manager = cast(Any, getattr(UploadJob, "objects"))
    job = upload_job_manager.select_related("source_center", "sensitive_meta").get(
        id=job_id
    )
    if job.status == UploadJob.Status.ANONYMIZED:
        return True

    if not job.file or not job.file.name:
        job.mark_lost("Upload job has no stored file")
        return False

    center = job.source_center
    if center is None:
        job.mark_error("Upload job has no resolved source center")
        return False

    job.mark_processing()
    provenance = _update_upload_provenance(job, stored_upload_path=job.file.name)
    provenance.setdefault("stored_upload_path", job.file.name)
    job.save(update_fields=["processing_provenance", "updated_at"])

    source_materialized = False
    try:
        with _ensure_upload_job_local_file(job) as file_path:
            source_materialized = True
            try:
                processor_name = (
                    provenance.get("processor_name") or _default_processor_name()
                )
                if not processor_name:
                    raise ObjectDoesNotExist(
                        "No default EndoscopyProcessor is configured"
                    )

                video = VideoImportService().import_and_anonymize(
                    file_path=file_path,
                    center_name=center.name,
                    processor_name=processor_name,
                    retry=False,
                )
                sensitive_meta = (
                    video.sensitive_meta if isinstance(video, VideoFile) else None
                )
            except Exception:
                quarantine_path = _quarantine_upload_job_file(
                    job,
                    local_path=Path(file_path),
                )
                if quarantine_path is not None:
                    logger.warning(
                        "Upload job %s failed; source quarantined at %s.",
                        job_id,
                        quarantine_path,
                    )
                raise

        job.mark_completed(sensitive_meta=sensitive_meta)
        cleanup_upload_job_source(job)
        prediction_model_name = provenance.get("prediction_model_name")
        if isinstance(video, VideoFile) and prediction_model_name:
            try:
                ai_model = AiModel.objects.get(name=str(prediction_model_name))
                model_meta = ai_model.get_latest_version()
                prediction_dispatch = dispatch_video_temporal_inference(
                    video_id=video.pk,
                    model_meta_id=model_meta.pk,
                    replace_prediction_segments=True,
                    delete_frames_after=True,
                )
                _update_upload_provenance(
                    job,
                    prediction_task_id=prediction_dispatch.task_id,
                    prediction_history_id=prediction_dispatch.history_id,
                    prediction_queue=prediction_dispatch.queue,
                )
                job.save(update_fields=["processing_provenance", "updated_at"])
            except Exception as exc:
                logger.warning(
                    "Video upload job %s imported but prediction dispatch failed: %s",
                    job_id,
                    exc,
                )
        return True
    except (FileNotFoundError, OSError) as exc:
        if source_materialized:
            logger.exception("Upload job processing failed for %s: %s", job_id, exc)
            job.mark_error(str(exc))
        else:
            error_detail = f"Upload source could not be materialized from storage. {exc}"
            logger.exception("Upload job source missing for %s: %s", job_id, exc)
            job.mark_lost(error_detail)
        return False
    except Exception as exc:
        logger.exception("Upload job processing failed for %s: %s", job_id, exc)
        job.mark_error(str(exc))
        return False


def _run_watcher_upload_job_inline(
    *,
    upload_job: UploadJob,
    watched_path: Path,
    normalized_type: str,
    source_center: Center,
    processor_name: str | None = None,
) -> UploadJob:
    upload_job.refresh_from_db()
    upload_job.status = UploadJob.Status.PROCESSING
    upload_job.error_detail = ""
    _update_upload_provenance(
        upload_job,
        processing_handoff="inline",
        watcher_processing_path=str(watched_path),
    )
    upload_job.save(
        update_fields=[
            "status",
            "error_detail",
            "processing_provenance",
            "updated_at",
        ]
    )

    imported_media: RawPdfFile | VideoFile | None = None
    sensitive_meta: SensitiveMeta | None = None
    if normalized_type == "report":
        report = ReportImportService().import_and_anonymize(
            file_path=watched_path,
            center_name=source_center.name,
            retry=False,
        )
        imported_media = report if isinstance(report, RawPdfFile) else None
        sensitive_meta = (
            report.sensitive_meta if isinstance(report, RawPdfFile) else None
        )
    elif normalized_type == "video":
        if not processor_name:
            raise ObjectDoesNotExist("No default EndoscopyProcessor is configured")
        video = VideoImportService().import_and_anonymize(
            file_path=watched_path,
            center_name=source_center.name,
            processor_name=processor_name,
            retry=False,
        )
        imported_media = video if isinstance(video, VideoFile) else None
        sensitive_meta = video.sensitive_meta if isinstance(video, VideoFile) else None
    else:
        raise ValueError(f"Unsupported watcher file type: {normalized_type}")

    upload_job.mark_completed(sensitive_meta=sensitive_meta)
    _cleanup_persisted_watcher_source(upload_job)
    safe_unlink_file(watched_path, missing_ok=True)

    prediction_model_name = upload_job.processing_provenance.get(
        "prediction_model_name"
    )
    if isinstance(imported_media, VideoFile) and prediction_model_name:
        try:
            ai_model = AiModel.objects.get(name=str(prediction_model_name))
            model_meta = ai_model.get_latest_version()
            prediction_dispatch = dispatch_video_temporal_inference(
                video_id=imported_media.pk,
                model_meta_id=model_meta.pk,
                replace_prediction_segments=True,
                delete_frames_after=True,
            )
            _update_upload_provenance(
                upload_job,
                prediction_task_id=prediction_dispatch.task_id,
                prediction_history_id=prediction_dispatch.history_id,
                prediction_queue=prediction_dispatch.queue,
            )
            upload_job.save(update_fields=["processing_provenance", "updated_at"])
        except Exception as exc:
            logger.warning(
                "Watcher upload job %s imported but prediction dispatch failed: %s",
                upload_job.id,
                exc,
            )

    return upload_job


def start_upload_job_processing(
    *,
    upload_job: UploadJob,
    task_dispatcher: Any | None = None,
) -> str:
    provenance = _normalized_upload_provenance(
        ingest_mode=upload_job.ingest_mode,
        source_system=upload_job.source_system or "api",
        content_hash=upload_job.content_hash,
        source_center=upload_job.source_center,
        storage_class=upload_job.storage_class,
        storage_tier=upload_job.storage_tier,
        retention_policy=upload_job.retention_policy,
        processing_provenance=cast(
            UploadProvenance | None,
            upload_job.processing_provenance,
        ),
    )
    upload_job.processing_provenance = provenance
    handoff_mode = "celery" if task_dispatcher is not None else "inline"

    try:
        if task_dispatcher is not None:
            queue = queue_for_job_kind(HeavyJobKind.PIPELINE_INGEST)
            apply_async = getattr(task_dispatcher, "apply_async", None)
            if callable(apply_async):
                apply_async(
                    args=(str(upload_job.id),),
                    queue=queue,
                    routing_key=queue,
                )
            else:
                task_dispatcher.delay(str(upload_job.id))
        else:
            processed = process_upload_job(str(upload_job.id))
            if not processed:
                refreshed_job = (
                    cast(Any, getattr(UploadJob, "objects"))
                    .filter(id=upload_job.id)
                    .first()
                )
                error_detail = (
                    getattr(refreshed_job, "error_detail", "") if refreshed_job else ""
                )
                raise RuntimeError(error_detail or "Upload job processing failed")
    except Exception as exc:
        logger.exception(
            "Upload job processing handoff failed for %s: %s",
            upload_job.id,
            exc,
        )
        upload_job.mark_error(f"Failed to start processing: {exc}")
        raise

    if provenance.get("processing_handoff") != handoff_mode:
        _update_upload_provenance(upload_job, processing_handoff=handoff_mode)
        upload_job.save(update_fields=["processing_provenance", "updated_at"])

    return handoff_mode


def process_watcher_file(
    *,
    file_path: Path | str,
    file_type: str,
    center: Center | None = None,
    processor_name: str | None = None,
    prediction_model_name: str | None = None,
    source_system: str = "watcher",
) -> UploadJob:
    watched_path = Path(file_path)
    if not watched_path.exists():
        raise FileNotFoundError(f"Watcher file not found: {watched_path}")
    if local_study_server_mode_enabled():
        raise ValueError(
            "Raw watcher ingestion is disabled for local_study_server; "
            "use preanonymized_import with a validated sidecar."
        )

    _opportunistic_reap_watcher_sources()

    source_center = center or resolve_default_center()
    if source_center is None:
        raise ObjectDoesNotExist("No center is configured for watcher ingestion")
    emit_hub_audit_event(
        "hub.center_resolved",
        source_system=source_system,
        request_user=None,
        hub_mode=hub_mode_enabled(),
        declared_center_key=source_center.center_key if center is not None else None,
        declared_center_name=source_center.name if center is not None else None,
        resolved_center_key=source_center.center_key,
        allowed_center_id=None,
    )

    normalized_type = file_type.strip().lower()
    if normalized_type == "report":
        content_type = "application/pdf"
    elif normalized_type == "video":
        content_type = "video/mp4"
    else:
        raise ValueError(f"Unsupported watcher file type: {file_type}")

    upload_job, created = create_or_reuse_watcher_upload_job(
        file_path=watched_path,
        content_type=content_type,
        source_center=source_center,
        source_system=source_system,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        processing_provenance={
            "file_type": normalized_type,
            "prediction_model_name": prediction_model_name,
        },
    )
    if not created:
        safe_unlink_file(watched_path, missing_ok=True)
        return upload_job
    if upload_job.is_complete:
        if _upload_job_has_usable_media(upload_job):
            safe_unlink_file(watched_path, missing_ok=True)
            return upload_job

        upload_job.mark_error(
            "Upload job marked complete but no usable media artifact was found. Forcing re-ingest."
        )

    upload_job.mark_processing()
    _ = _update_upload_provenance(
        upload_job,
        watcher_processing_path=str(watched_path),
    )
    upload_job.save(update_fields=["processing_provenance", "updated_at"])

    try:
        if normalized_type == "video":
            effective_processor_name = processor_name or _default_processor_name()
            if not effective_processor_name:
                raise ObjectDoesNotExist("No default EndoscopyProcessor is configured")
            _ = _update_upload_provenance(
                upload_job,
                watcher_processing_path=str(watched_path),
                processor_name=effective_processor_name,
            )
            upload_job.save(update_fields=["processing_provenance", "updated_at"])

        start_upload_job_processing(
            upload_job=upload_job,
            task_dispatcher=_upload_job_task_dispatcher(),
        )
        safe_unlink_file(watched_path, missing_ok=True)
        return upload_job
    except Exception as exc:
        if _is_celery_broker_connection_error(exc):
            logger.warning(
                "Watcher Celery handoff failed for %s; processing inline: %s",
                watched_path,
                exc,
            )
            try:
                return _run_watcher_upload_job_inline(
                    upload_job=upload_job,
                    watched_path=watched_path,
                    normalized_type=normalized_type,
                    source_center=source_center,
                    processor_name=(
                        effective_processor_name
                        if normalized_type == "video"
                        else None
                    ),
                )
            except Exception as inline_exc:
                exc = inline_exc

        logger.exception(
            "Watcher processing handoff failed for %s: %s",
            watched_path,
            exc,
        )
        upload_job.mark_error(str(exc))
        # Move the failed file to quarantine
        try:
            quarantine_path = _quarantine_dir() / watched_path.name
            atomic_move_file(source=watched_path, destination=quarantine_path)
            _update_upload_provenance(upload_job, quarantined_path=str(quarantine_path))
            upload_job.save(update_fields=["processing_provenance", "updated_at"])
            logger.warning(
                "File %s moved to quarantine: %s", watched_path, quarantine_path
            )
        except Exception as move_exc:
            logger.error(
                "Failed to move file %s to quarantine during error handling: %s",
                watched_path,
                move_exc,
            )
        _cleanup_persisted_watcher_source(upload_job)
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

    _opportunistic_reap_watcher_sources()

    suffix = watched_path.suffix.lower()
    if suffix == ".pdf":
        normalized_type = "report"
        content_type = "application/pdf"
    elif suffix == ".mp4":
        normalized_type = "video"
        content_type = "video/mp4"
    elif suffix == ".txt":
        normalized_type = "report"
        content_type = "export/txt"
    else:
        raise ValueError(
            f"Unsupported preanonymized watcher file suffix: {watched_path.suffix}"
        )

    strict_local = local_study_server_mode_enabled()
    sidecar_path = watched_path.with_suffix(".json")
    try:
        if strict_local:
            _validate_local_preanonymized_drop_path(watched_path)
        metadata_payload, loaded_sidecar_path = _load_preanonymized_sidecar(
            watched_path,
            strict=strict_local,
        )
        sidecar_path = loaded_sidecar_path or sidecar_path
        if strict_local:
            assert metadata_payload is not None
            declared_hash = (metadata_payload.file_sha256 or "").strip().lower()
            actual_hash = sha256_file(watched_path)
            if declared_hash != actual_hash:
                raise ValueError(
                    "Preanonymized sidecar file_sha256 does not match media file"
                )

        if strict_local:
            assert metadata_payload is not None
            source_center, center_error = resolve_declared_upload_center(
                center_key=metadata_payload.center_key,
                center_name=None,
            )
            if center_error:
                raise ValueError(center_error)
            if source_center is None:
                raise ObjectDoesNotExist(
                    "No center is configured for preanonymized watcher ingestion"
                )
            if center is not None and center.pk != source_center.pk:
                raise ValueError(
                    "Declared sidecar center_key does not match watcher center"
                )
        else:
            declared_center = None
            if metadata_payload is not None:
                declared_center, center_error = resolve_declared_upload_center(
                    center_key=metadata_payload.center_key,
                    center_name=metadata_payload.center_name,
                )
                if center_error:
                    raise ValueError(center_error)
            source_center = center or declared_center or resolve_default_center()
            if source_center is None:
                raise ObjectDoesNotExist(
                    "No center is configured for watcher ingestion"
                )
    except Exception as exc:
        if strict_local:
            _quarantine_preanonymized_drop(
                media_path=watched_path,
                sidecar_path=sidecar_path,
            )
            emit_hub_audit_event(
                "hub.preanonymized_drop_rejected",
                source_system=source_system,
                request_user=None,
                hub_mode=hub_mode_enabled(),
                watched_path=str(watched_path),
                sidecar_path=str(sidecar_path),
                reason=str(exc),
            )
        raise

    emit_hub_audit_event(
        "hub.center_resolved",
        source_system=source_system,
        request_user=None,
        hub_mode=hub_mode_enabled(),
        declared_center_key=source_center.center_key if center is not None else None,
        declared_center_name=source_center.name if center is not None else None,
        resolved_center_key=source_center.center_key,
        allowed_center_id=None,
    )

    upload_job, created = create_or_reuse_watcher_upload_job(
        file_path=watched_path,
        content_type=content_type,
        source_center=source_center,
        source_system=source_system,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        processing_provenance={
            "file_type": normalized_type,
            "ingest_variant": "preanonymized",
            "sidecar_path": str(sidecar_path) if sidecar_path is not None else "",
            "sidecar_payload": (
                metadata_payload.model_dump(mode="json", exclude_none=True)
                if metadata_payload is not None
                else {}
            ),
        },
    )
    if not created:
        safe_unlink_file(watched_path, missing_ok=True)
        if sidecar_path is not None and sidecar_path.exists():
            safe_unlink_file(sidecar_path, missing_ok=True)
        return upload_job
    if upload_job.is_complete:
        if _upload_job_has_usable_media(upload_job):
            safe_unlink_file(watched_path, missing_ok=True)
            if sidecar_path is not None and sidecar_path.exists():
                safe_unlink_file(sidecar_path, missing_ok=True)
            return upload_job

        upload_job.mark_error(
            "Upload job marked complete but no usable media artifact was found. Forcing re-ingest."
        )

    upload_job.mark_processing()
    _ = _update_upload_provenance(
        upload_job,
        watcher_processing_path=str(watched_path),
    )
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
            _ = _update_upload_provenance(
                upload_job,
                watcher_processing_path=str(watched_path),
                processor_name=processor_name or _default_processor_name(),
            )
            sensitive_meta = video.sensitive_meta

        if sidecar_path is not None and sidecar_path.exists():
            safe_unlink_file(sidecar_path, missing_ok=True)
        upload_job.save(update_fields=["processing_provenance", "updated_at"])
        upload_job.mark_completed(sensitive_meta=sensitive_meta)
        cleanup_upload_job_source(upload_job)
        emit_hub_audit_event(
            "hub.preanonymized_drop_accepted",
            upload_job_id=str(upload_job.id),
            source_system=source_system,
            request_user=None,
            center_key=source_center.center_key,
            watched_path=str(watched_path),
            sidecar_path=str(sidecar_path) if sidecar_path is not None else None,
            content_hash=upload_job.content_hash,
        )
        return upload_job
    except Exception as exc:
        logger.exception(
            "Preanonymized watcher processing failed for %s: %s",
            watched_path,
            exc,
        )
        upload_job.mark_error(str(exc))
        emit_hub_audit_event(
            "hub.preanonymized_drop_rejected",
            upload_job_id=str(upload_job.id),
            source_system=source_system,
            request_user=None,
            center_key=source_center.center_key,
            watched_path=str(watched_path),
            sidecar_path=str(sidecar_path) if sidecar_path is not None else None,
            reason=str(exc),
        )
        # Move the failed file to quarantine
        try:
            quarantine_path = _quarantine_dir() / watched_path.name
            atomic_move_file(source=watched_path, destination=quarantine_path)
            _update_upload_provenance(upload_job, quarantined_path=str(quarantine_path))
            upload_job.save(update_fields=["processing_provenance"])
            logger.warning(
                "File %s moved to quarantine: %s", watched_path, quarantine_path
            )
        except Exception as move_exc:
            logger.error(
                "Failed to move file %s to quarantine during error handling: %s",
                watched_path,
                move_exc,
            )
        # Also attempt to move the sidecar if it exists
        if sidecar_path is not None and sidecar_path.exists():
            try:
                quarantine_sidecar_path = _quarantine_dir() / sidecar_path.name
                atomic_move_file(
                    source=sidecar_path, destination=quarantine_sidecar_path
                )
                _update_upload_provenance(
                    upload_job,
                    quarantined_sidecar_path=str(quarantine_sidecar_path),
                )
                upload_job.save(update_fields=["processing_provenance", "updated_at"])
                logger.warning(
                    "Sidecar %s moved to quarantine: %s",
                    sidecar_path,
                    quarantine_sidecar_path,
                )
            except Exception as move_exc:
                logger.error(
                    "Failed to move sidecar %s to quarantine during error handling: %s",
                    sidecar_path,
                    move_exc,
                )
        _cleanup_persisted_watcher_source(upload_job)
        raise
