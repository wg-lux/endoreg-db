# pyright: reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations
import os
import uuid
import json
import logging
import hashlib
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, NotRequired, Protocol, TypedDict, cast
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import AnonymousUser
from django.core.files import File
from django.core.files.uploadedfile import UploadedFile
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, OperationalError, transaction
from kombu.exceptions import OperationalError as KombuOperationalError
from pydantic import ValidationError
import yaml

from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient_external_id import (
    PatientExternalID,
)
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.services.center_access import resolve_allowed_center_ids
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.jobs.heavy_jobs import (
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
from endoreg_db.services.hub.import_monitoring import (
    schedule_dispatch_retry,
    schedule_processing_retry,
    schedule_storage_retry,
)
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportLease,
    UploadJobImportLeaseBusy,
    UploadJobImportLeaseHeartbeat,
    UploadJobImportLeaseLost,
    acquire_upload_job_import_lease,
    locked_upload_job_import_lease,
    release_upload_job_import_lease,
)
from endoreg_db.services.hub.quarantine import index_quarantine_file
from endoreg_db.services.hub.watcher_handoff import (
    WatcherFileNotReadyError,
    assert_watcher_file_unchanged as _assert_watcher_file_unchanged,
    wait_for_watcher_file_ready as _wait_for_watcher_file_ready,
    watcher_path_reference_text,
)
from endoreg_db.services.hub.payloads import PreanonymizedIngestPayload
from endoreg_db.services.hub.payloads import LocalStudyServerPreanonymizedIngestPayload
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.set_default_center import (
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
from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.permissions import is_debug_mode
from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    path_reference,
    safe_log_value,
)
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue

STALE_UPLOAD_JOB_AGE = timedelta(hours=2)
LOCK_RETRY_ATTEMPTS = 10
logger = logging.getLogger(__name__)
WATCHER_CLEANUP_BATCH_LIMIT = 512


class CeleryTaskDispatcher(Protocol):
    def apply_async(self, *args: Any, **kwargs: Any) -> Any: ...


class DelayTaskDispatcher(Protocol):
    def delay(self, *args: Any, **kwargs: Any) -> Any: ...


class _SensitiveMetaLinkIds(Protocol):
    external_id_id: int | None
    pseudo_patient_id: int | None
    pseudo_examination_id: int | None


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
    if upload_job.ingest_mode != UploadJob.IngestMode.WATCHER.value:
        return False
    if (
        upload_job.retention_policy
        != UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS.value
    ):
        return False
    if not upload_job.source_file_persisted:
        return False

    update_fields = ["updated_at"]
    if upload_job.source_file_delete_eligible_at is None:
        upload_job.source_file_delete_eligible_at = timezone.now()
        update_fields.append("source_file_delete_eligible_at")
    if upload_job.cleanup_status != UploadJob.CleanupStatus.ELIGIBLE.value:
        upload_job.cleanup_status = UploadJob.CleanupStatus.ELIGIBLE.value
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
    sidecar_payload: JsonObject
    watcher_processing_path: str
    processor_name: str | None
    processing_handoff: str
    llm_job_id: str
    llm_task_id: str
    llm_queue: str
    prediction_model_name: str | None
    prediction_task_id: str
    prediction_history_id: int | None
    prediction_queue: str
    video_import_task_id: str
    video_import_queue: str
    video_import_fencing_epoch: int
    stored_upload_path: str
    quarantined_path: str
    quarantined_sidecar_path: str
    media_integrity_status: str
    media_integrity_reason: str
    media_integrity_missing_artifacts: list[str]
    previous_upload_job_id: str
    custom_marker: NotRequired[str]


class _NamedUploadFile(Protocol):
    name: str


def _upload_provenance(
    existing: JsonObject | UploadProvenance | None = None,
) -> UploadProvenance:
    provenance: dict[str, JsonValue] = {}
    if existing:
        provenance.update(cast(JsonObject, existing))
    return cast(UploadProvenance, provenance)


def _update_upload_provenance(
    upload_job: UploadJob,
    **updates: JsonValue | None,
) -> UploadProvenance:
    provenance: dict[str, JsonValue] = dict(
        cast(JsonObject, _upload_provenance(upload_job.processing_provenance))
    )
    for key, value in updates.items():
        if value is not None:
            provenance[key] = value
    upload_job.processing_provenance = provenance
    return cast(UploadProvenance, provenance)


def record_active_learning_selection_provenance(
    upload_job: UploadJob,
    *,
    selection_strategy: str = "temporal_segment_hybrid",
    candidate_count: int,
    selected_count: int,
    annotation_budget: int,
    ai_dataset_id: int | None = None,
    model_meta_id: int | None = None,
    extra_metadata: JsonObject | None = None,
    save: bool = True,
) -> UploadProvenance:
    active_learning_payload: JsonObject = {
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

    existing_sidecar_payload: JsonObject = {}
    current_sidecar_payload = upload_job.processing_provenance.get("sidecar_payload")
    if isinstance(current_sidecar_payload, dict):
        existing_sidecar_payload.update(cast(JsonObject, current_sidecar_payload))
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


def _compute_uploaded_file_content_hash(uploaded_file: UploadedFile) -> str:
    digest = hashlib.sha256()
    uploaded_file.seek(0)
    for chunk in cast(Iterable[bytes], uploaded_file.chunks()):
        digest.update(chunk)
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
    """Compatibility adapter for callers that only support one center."""
    if (
        not user
        or isinstance(user, AnonymousUser)
        or not getattr(user, "is_authenticated", False)
    ):
        return None
    allowed_center_ids = resolve_allowed_center_ids(user)
    if allowed_center_ids is None:
        return None
    if not allowed_center_ids:
        return -1
    if len(allowed_center_ids) != 1:
        raise RuntimeError(
            "Multiple center assignments require resolve_allowed_center_ids()."
        )
    return next(iter(allowed_center_ids))


def _is_authenticated_user(user: Any) -> bool:
    return bool(
        user
        and not isinstance(user, AnonymousUser)
        and getattr(user, "is_authenticated", False)
    )


def _api_upload_context_error(
    *,
    error: str,
    hub_mode: bool,
    allowed_center_id: int | None = None,
    include_deployment_role: bool = True,
) -> tuple[None, int | None, str, dict[str, Any]]:
    details: dict[str, Any] = {"hub_mode": hub_mode}
    if include_deployment_role:
        details["local_study_server"] = local_study_server_mode_enabled()
    return None, allowed_center_id, error, details


def _single_allowed_center_id(
    allowed_center_ids: frozenset[int] | None,
) -> int | None:
    if allowed_center_ids is None or len(allowed_center_ids) != 1:
        return None
    return next(iter(allowed_center_ids))


def _center_is_outside_scope(
    center: Center | None,
    allowed_center_ids: frozenset[int] | None,
) -> bool:
    return bool(
        allowed_center_ids is not None
        and center is not None
        and center.pk not in allowed_center_ids
    )


def _resolve_api_source_center(
    *,
    strict_center_mode: bool,
    declared_center: Center | None,
    user: Any,
    center_key: str,
    center_name: str,
) -> Center | None:
    if strict_center_mode:
        return declared_center
    return resolve_upload_center(
        user=user,
        center_key=center_key,
        center_name=center_name,
    )


def _successful_api_upload_context(
    *,
    source_center: Center | None,
    allowed_center_id: int | None,
    hub_mode: bool,
    center_key: str,
    center_name: str,
) -> tuple[Center | None, int | None, None, dict[str, Any]]:
    return (
        source_center,
        allowed_center_id,
        None,
        {
            "hub_mode": hub_mode,
            "local_study_server": local_study_server_mode_enabled(),
            "declared_center_key": center_key or None,
            "declared_center_name": center_name or None,
            "resolved_center_key": (
                source_center.center_key if source_center is not None else None
            ),
        },
    )


def _strict_api_authentication_error(
    *,
    strict_center_mode: bool,
    authenticated: bool,
) -> str | None:
    if strict_center_mode and not authenticated:
        return "Authentication is required for center-scoped API uploads."
    return None


def _strict_api_center_error(
    *,
    strict_center_mode: bool,
    center_key: str,
    declared_center: Center | None,
) -> str | None:
    if strict_center_mode and (not center_key or declared_center is None):
        return "center_key is required for center-scoped API uploads."
    return None


def _api_center_access_error(
    *,
    declared_center: Center | None,
    allowed_center_ids: frozenset[int] | None,
) -> str | None:
    if allowed_center_ids == frozenset():
        return "You do not have access to upload jobs."
    if _center_is_outside_scope(declared_center, allowed_center_ids):
        return "Upload center is outside the authenticated scope"
    return None


def _resolve_api_declared_center_context(
    *,
    strict_center_mode: bool,
    center_key: str,
    center_name: str,
) -> tuple[Center | None, str | None]:
    declared_center, resolution_error = resolve_declared_upload_center(
        center_key=center_key,
        center_name=center_name,
    )
    if resolution_error is not None:
        return None, resolution_error
    return declared_center, _strict_api_center_error(
        strict_center_mode=strict_center_mode,
        center_key=center_key,
        declared_center=declared_center,
    )


def _allowed_api_center_ids(
    *,
    user: Any,
    authenticated: bool,
) -> frozenset[int] | None:
    if is_debug_mode() and not authenticated:
        return None
    return resolve_allowed_center_ids(user)


def _emit_api_center_resolved(
    *,
    user: Any,
    hub_mode: bool,
    center_key: str,
    center_name: str,
    source_center: Center | None,
    allowed_center_id: int | None,
) -> None:
    emit_hub_audit_event(
        "hub.center_resolved",
        source_system="api",
        request_user=user,
        hub_mode=hub_mode,
        declared_center_key=center_key or None,
        declared_center_name=center_name or None,
        resolved_center_key=(
            source_center.center_key if source_center is not None else None
        ),
        allowed_center_id=allowed_center_id,
    )


def _normalized_center_identity(
    *,
    center_key: str | None,
    center_name: str | None,
) -> tuple[str, str]:
    return (center_key or "").strip(), (center_name or "").strip()


def resolve_api_upload_context(
    *,
    user: Any = None,
    center_key: str | None = None,
    center_name: str | None = None,
) -> tuple[Center | None, int | None, str | None, dict[str, Any]]:
    normalized_center_key, normalized_center_name = _normalized_center_identity(
        center_key=center_key,
        center_name=center_name,
    )
    hub_mode = hub_mode_enabled()
    strict_center_mode = strict_center_upload_mode_enabled()
    authenticated = _is_authenticated_user(user)
    authentication_error = _strict_api_authentication_error(
        strict_center_mode=strict_center_mode,
        authenticated=authenticated,
    )
    if authentication_error is not None:
        return _api_upload_context_error(
            error=authentication_error,
            hub_mode=hub_mode,
        )

    declared_center, center_resolution_error = _resolve_api_declared_center_context(
        strict_center_mode=strict_center_mode,
        center_key=normalized_center_key,
        center_name=normalized_center_name,
    )
    if center_resolution_error is not None:
        return _api_upload_context_error(
            error=center_resolution_error,
            hub_mode=hub_mode,
        )

    allowed_center_ids = _allowed_api_center_ids(
        user=user,
        authenticated=authenticated,
    )
    allowed_center_id = _single_allowed_center_id(allowed_center_ids)
    access_error = _api_center_access_error(
        declared_center=declared_center,
        allowed_center_ids=allowed_center_ids,
    )
    if access_error is not None:
        return _api_upload_context_error(
            error=access_error,
            hub_mode=hub_mode,
            allowed_center_id=allowed_center_id,
        )

    source_center = _resolve_api_source_center(
        strict_center_mode=strict_center_mode,
        declared_center=declared_center,
        user=user,
        center_key=normalized_center_key,
        center_name=normalized_center_name,
    )
    _emit_api_center_resolved(
        user=user,
        hub_mode=hub_mode,
        center_key=normalized_center_key,
        center_name=normalized_center_name,
        source_center=source_center,
        allowed_center_id=allowed_center_id,
    )
    if _center_is_outside_scope(source_center, allowed_center_ids):
        return _api_upload_context_error(
            error="Upload center is outside the authenticated scope",
            hub_mode=hub_mode,
            allowed_center_id=allowed_center_id,
            include_deployment_role=False,
        )

    return _successful_api_upload_context(
        source_center=source_center,
        allowed_center_id=allowed_center_id,
        hub_mode=hub_mode,
        center_key=normalized_center_key,
        center_name=normalized_center_name,
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
    *,
    lease: UploadJobImportLease,
) -> Generator[Path, None, None]:
    try:
        with ensure_local_file(job.file) as file_path:
            yield Path(file_path)
            return
    except OSError as storage_exc:
        fallback_path = _safe_existing_media_root_path(job.file.name)
        if fallback_path is None:
            raise storage_exc
        fallback_hash = sha256_file(fallback_path)
        with locked_upload_job_import_lease(lease) as owned_job:
            if owned_job.content_hash and fallback_hash != owned_job.content_hash:
                raise IOError(
                    "Fallback upload source failed content-hash verification"
                ) from storage_exc
            if not owned_job.content_hash:
                owned_job.content_hash = fallback_hash
                owned_job.save(update_fields=["content_hash", "updated_at"])
            job.content_hash = owned_job.content_hash
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
) -> tuple[UploadJob, UploadJobImportLease | None, bool]:
    upload_job_manager = UploadJob.objects
    with transaction.atomic():
        job = (
            upload_job_manager.select_for_update(of=("self",))
            .select_related(
                "source_center",
                "sensitive_meta",
            )
            .get(id=upload_job_id)
        )
        if job.status == UploadJob.Status.ANONYMIZED.value:
            return job, None, False
        if not job.file or not job.file.name:
            job.mark_lost("Upload job has no stored file")
            return job, None, False
        if job.source_center is None:
            job.mark_error(
                "Upload job has no resolved source center",
                error_code=UploadJob.ErrorCode.INVALID_CONFIGURATION.value,
            )
            return job, None, False

        provenance = _upload_provenance(
            cast(UploadProvenance | None, job.processing_provenance)
        )
        existing_task_id = provenance.get("video_import_task_id")
        if (
            job.status == UploadJob.Status.PROCESSING.value
            and job.retry_count == 0
            and not job.processing_lease_owner
            and isinstance(existing_task_id, str)
            and existing_task_id.strip()
            and timezone.now() - job.updated_at <= STALE_UPLOAD_JOB_AGE
        ):
            # Compatibility for jobs that were already processing when the
            # lease migration was deployed. New reservations always use leases.
            return job, None, False

        try:
            lease = acquire_upload_job_import_lease(
                upload_job_id=str(job.pk),
                owner=task_id,
            )
        except UploadJobImportLeaseBusy:
            return job, None, False

        job.mark_processing()
        _update_upload_provenance(
            job,
            stored_upload_path=job.file.name,
            processing_handoff="ffmpeg_media",
            video_import_task_id=task_id,
            video_import_queue=queue,
            video_import_fencing_epoch=lease.fencing_epoch,
        )
        job.save(
            update_fields=[
                "processing_provenance",
                "updated_at",
            ]
        )
        return job, lease, True


def _media_integrity_provenance(
    result: MediaIntegrityResult,
    *,
    previous_upload_job_id: uuid.UUID | str | None = None,
) -> JsonObject:
    provenance: JsonObject = {
        "media_integrity_status": result.status.value,
        "media_integrity_reason": result.reason,
    }
    if result.missing_artifacts:
        provenance["media_integrity_missing_artifacts"] = list(result.missing_artifacts)
    if previous_upload_job_id is not None:
        provenance["previous_upload_job_id"] = str(previous_upload_job_id)
    return provenance


@dataclass(frozen=True)
class _UploadJobCreateContext:
    uploaded_file: UploadedFile | _NamedUploadFile | None
    content_type: str
    created_by: object | None
    source_center: Center | None
    source_system: str
    content_hash: str
    idempotency_key: str
    ingest_mode: str
    storage_class: str
    storage_tier: str
    retention_policy: str
    source_file_persisted: bool
    cleanup_status: str
    processing_provenance: UploadProvenance


@dataclass(frozen=True)
class _InvalidUploadJobReuse:
    job_id: str
    reason: str
    status: str
    integrity_result: MediaIntegrityResult | None = None


def _normalized_upload_content_hash(
    *,
    uploaded_file: UploadedFile | _NamedUploadFile | None,
    content_hash: str,
) -> str:
    normalized_content_hash = (content_hash or "").strip()
    if normalized_content_hash:
        return normalized_content_hash
    if not isinstance(uploaded_file, UploadedFile):
        raise ValueError("uploaded_file is required when content_hash is blank")
    return _compute_uploaded_file_content_hash(uploaded_file)


def _matching_active_upload_job(
    context: _UploadJobCreateContext,
) -> UploadJob | None:
    existing_job_qs = (
        UploadJob.objects.filter(
            source_center=context.source_center,
            content_type=context.content_type,
        )
        .exclude(status__in=[UploadJob.Status.ERROR, UploadJob.Status.LOST])
        .select_for_update()
    )
    if context.content_hash:
        existing_job = existing_job_qs.filter(content_hash=context.content_hash).first()
        if existing_job is not None:
            return existing_job
    if not context.idempotency_key:
        return None
    return existing_job_qs.filter(
        idempotency_key=context.idempotency_key,
        source_system=context.source_system,
        ingest_mode=context.ingest_mode,
        storage_class=context.storage_class,
        storage_tier=context.storage_tier,
    ).first()


def _assess_active_upload_job_reuse(
    existing_job: UploadJob,
) -> _InvalidUploadJobReuse | None:
    updated_at = getattr(existing_job, "updated_at", None)
    if updated_at and timezone.now() - updated_at <= STALE_UPLOAD_JOB_AGE:
        return None
    return _InvalidUploadJobReuse(
        job_id=str(existing_job.id),
        reason=(
            "Existing upload job was stale in pending/processing state. "
            "Forcing re-ingest."
        ),
        status=UploadJob.Status.ERROR.value,
    )


def _assess_completed_upload_job_reuse(
    existing_job: UploadJob,
) -> _InvalidUploadJobReuse | None:
    integrity_result = check_upload_job_media_integrity(existing_job)
    if integrity_result.ok:
        return None
    return _InvalidUploadJobReuse(
        job_id=str(existing_job.id),
        reason=(
            "Completed upload job failed media integrity check: "
            f"{integrity_result.reason} Forcing re-ingest."
        ),
        status=UploadJob.Status.LOST.value,
        integrity_result=integrity_result,
    )


def _assess_upload_job_reuse(
    existing_job: UploadJob,
) -> _InvalidUploadJobReuse | None:
    if existing_job.status in {
        UploadJob.Status.PENDING,
        UploadJob.Status.PROCESSING,
    }:
        return _assess_active_upload_job_reuse(existing_job)
    if existing_job.status == UploadJob.Status.ANONYMIZED.value:
        return _assess_completed_upload_job_reuse(existing_job)
    return _InvalidUploadJobReuse(
        job_id=str(existing_job.id),
        reason="Previous job was incomplete or invalid for reuse. Forcing re-ingest.",
        status=UploadJob.Status.ERROR.value,
    )


def _audit_reused_upload_job(
    *,
    existing_job: UploadJob,
    context: _UploadJobCreateContext,
) -> None:
    emit_hub_audit_event(
        "hub.upload_job_reused",
        upload_job_id=str(existing_job.id),
        source_system=context.source_system,
        request_user=context.created_by,
        center_key=(
            context.source_center.center_key
            if context.source_center is not None
            else None
        ),
        ingest_mode=context.ingest_mode,
        idempotency_key=context.idempotency_key,
    )


def _authenticated_upload_creator(created_by: object | None) -> object | None:
    if getattr(created_by, "is_authenticated", False):
        return created_by
    return None


def _create_upload_job(
    *,
    context: _UploadJobCreateContext,
    reingest_provenance_updates: JsonObject,
) -> UploadJob:
    if isinstance(context.uploaded_file, UploadedFile):
        context.uploaded_file.seek(0)
    return UploadJob.objects.create(
        file=context.uploaded_file,
        content_type=context.content_type,
        source_center=context.source_center,
        source_system=context.source_system,
        content_hash=context.content_hash,
        idempotency_key=context.idempotency_key,
        ingest_mode=context.ingest_mode,
        storage_class=context.storage_class,
        storage_tier=context.storage_tier,
        retention_policy=context.retention_policy,
        source_file_persisted=context.source_file_persisted,
        cleanup_status=context.cleanup_status,
        original_filename=getattr(context.uploaded_file, "name", "") or "",
        processing_provenance=_normalized_upload_provenance(
            ingest_mode=context.ingest_mode,
            source_system=context.source_system,
            content_hash=context.content_hash,
            source_center=context.source_center,
            storage_class=context.storage_class,
            storage_tier=context.storage_tier,
            retention_policy=context.retention_policy,
            processing_provenance=cast(
                UploadProvenance,
                {
                    **context.processing_provenance,
                    **reingest_provenance_updates,
                },
            ),
        ),
        created_by=_authenticated_upload_creator(context.created_by),
    )


def _audit_created_upload_job(
    *,
    job: UploadJob,
    context: _UploadJobCreateContext,
) -> None:
    emit_hub_audit_event(
        "hub.upload_job_created",
        upload_job_id=str(job.id),
        source_system=context.source_system,
        request_user=context.created_by,
        center_key=(
            context.source_center.center_key
            if context.source_center is not None
            else None
        ),
        ingest_mode=context.ingest_mode,
        content_hash=context.content_hash,
        idempotency_key=context.idempotency_key,
        storage_tier=context.storage_tier,
        retention_policy=context.retention_policy,
    )


def _create_upload_job_after_conflict_check(
    *,
    context: _UploadJobCreateContext,
    reingest_provenance_updates: JsonObject,
) -> tuple[UploadJob, bool]:
    try:
        job = _create_upload_job(
            context=context,
            reingest_provenance_updates=reingest_provenance_updates,
        )
    except IntegrityError:
        conflict_job = _matching_active_upload_job(context)
        if conflict_job is not None:
            return conflict_job, False
        raise
    _audit_created_upload_job(job=job, context=context)
    return job, True


def _attempt_upload_job_create_or_reuse(
    *,
    context: _UploadJobCreateContext,
    reingest_provenance_updates: JsonObject,
) -> tuple[UploadJob, bool] | _InvalidUploadJobReuse:
    with transaction.atomic():
        existing_job = _matching_active_upload_job(context)
        if existing_job is None:
            return _create_upload_job_after_conflict_check(
                context=context,
                reingest_provenance_updates=reingest_provenance_updates,
            )
        invalid_reuse = _assess_upload_job_reuse(existing_job)
        if invalid_reuse is None:
            _audit_reused_upload_job(existing_job=existing_job, context=context)
            return existing_job, False
        logger.warning(
            "UploadJob %s found but not valid for reuse (status: %s). %s",
            existing_job.id,
            existing_job.status,
            invalid_reuse.reason,
        )
        return invalid_reuse


def _audit_upload_job_media_integrity_failure(
    *,
    invalid_job: UploadJob,
    integrity_result: MediaIntegrityResult,
    created_by: object | None,
) -> None:
    emit_hub_audit_event(
        "hub.upload_job_media_integrity_failed",
        upload_job_id=str(invalid_job.id),
        source_system=invalid_job.source_system,
        request_user=created_by,
        center_key=(
            invalid_job.source_center.center_key
            if invalid_job.source_center is not None
            else None
        ),
        ingest_mode=invalid_job.ingest_mode,
        content_hash=invalid_job.content_hash,
        media_integrity_status=integrity_result.status.value,
        media_integrity_reason=integrity_result.reason,
        missing_artifacts=list(integrity_result.missing_artifacts),
    )


def _record_invalid_upload_integrity(
    *,
    invalid_job: UploadJob,
    invalid_reuse: _InvalidUploadJobReuse,
    created_by: object | None,
) -> JsonObject:
    integrity_result = invalid_reuse.integrity_result
    if integrity_result is None:
        return {}
    provenance_updates = _media_integrity_provenance(integrity_result)
    _update_upload_provenance(invalid_job, **provenance_updates)
    invalid_job.save(update_fields=["processing_provenance", "updated_at"])
    _audit_upload_job_media_integrity_failure(
        invalid_job=invalid_job,
        integrity_result=integrity_result,
        created_by=created_by,
    )
    return _media_integrity_provenance(
        integrity_result,
        previous_upload_job_id=invalid_job.id,
    )


def _mark_invalid_upload_job(
    *,
    invalid_job: UploadJob,
    invalid_reuse: _InvalidUploadJobReuse,
) -> None:
    if invalid_reuse.status == UploadJob.Status.LOST.value:
        invalid_job.mark_lost(
            invalid_reuse.reason,
            error_code=UploadJob.ErrorCode.MEDIA_INTEGRITY_FAILED.value,
        )
        return
    invalid_job.mark_error(
        invalid_reuse.reason,
        error_code=UploadJob.ErrorCode.MEDIA_INTEGRITY_FAILED.value,
    )


def _invalidate_upload_job_for_reingest(
    *,
    invalid_reuse: _InvalidUploadJobReuse,
    created_by: object | None,
) -> JsonObject:
    invalid_job = (
        UploadJob.objects.filter(pk=invalid_reuse.job_id)
        .exclude(status__in=[UploadJob.Status.ERROR, UploadJob.Status.LOST])
        .first()
    )
    if invalid_job is None:
        return {}
    reingest_updates = _record_invalid_upload_integrity(
        invalid_job=invalid_job,
        invalid_reuse=invalid_reuse,
        created_by=created_by,
    )
    _mark_invalid_upload_job(
        invalid_job=invalid_job,
        invalid_reuse=invalid_reuse,
    )
    _cleanup_persisted_watcher_source(invalid_job)
    return reingest_updates


def _handle_upload_job_lock_error(
    *,
    exc: OperationalError,
    attempt: int,
    context: _UploadJobCreateContext,
) -> None:
    if not _is_retryable_db_lock_error(exc) or attempt == LOCK_RETRY_ATTEMPTS:
        raise exc
    logger.warning(
        "UploadJob create/reuse hit a locked database for source_system=%s "
        "idempotency_key=%s attempt=%d/%d; retrying.",
        context.source_system,
        context.idempotency_key,
        attempt,
        LOCK_RETRY_ATTEMPTS,
    )
    time.sleep(0.1 * attempt)


def create_or_reuse_upload_job(
    *,
    uploaded_file: UploadedFile | _NamedUploadFile | None,
    content_type: str,
    created_by: object | None = None,
    source_center: Center | None = None,
    source_system: str = "api",
    content_hash: str = "",
    idempotency_key: str = "",
    ingest_mode: str = UploadJob.IngestMode.API.value,
    storage_class: str = UploadJob.StorageClass.INGEST.value,
    storage_tier: str = UploadJob.StorageTier.UPLOAD_API.value,
    retention_policy: str = UploadJob.RetentionPolicy.PRESERVE_SOURCE.value,
    source_file_persisted: bool = True,
    cleanup_status: str = UploadJob.CleanupStatus.PENDING.value,
    processing_provenance: UploadProvenance | None = None,
    allow_completed_reuse_without_media: bool = False,
) -> tuple[UploadJob, bool]:
    del allow_completed_reuse_without_media
    context = _UploadJobCreateContext(
        uploaded_file=uploaded_file,
        content_type=content_type,
        created_by=created_by,
        source_center=source_center,
        source_system=source_system,
        content_hash=_normalized_upload_content_hash(
            uploaded_file=uploaded_file,
            content_hash=content_hash,
        ),
        idempotency_key=(idempotency_key or "").strip(),
        ingest_mode=ingest_mode,
        storage_class=storage_class,
        storage_tier=storage_tier,
        retention_policy=retention_policy,
        source_file_persisted=source_file_persisted,
        cleanup_status=cleanup_status,
        processing_provenance=_upload_provenance(processing_provenance),
    )
    reingest_provenance_updates: JsonObject = {}

    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            result = _attempt_upload_job_create_or_reuse(
                context=context,
                reingest_provenance_updates=reingest_provenance_updates,
            )
            if not isinstance(result, _InvalidUploadJobReuse):
                return result
            reingest_provenance_updates = _invalidate_upload_job_for_reingest(
                invalid_reuse=result,
                created_by=created_by,
            )
        except OperationalError as exc:
            _handle_upload_job_lock_error(
                exc=exc,
                attempt=attempt,
                context=context,
            )
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
    settled_stat: os.stat_result | None = None,
) -> tuple[UploadJob, bool]:
    file_path = Path(file_path)
    entrypoint_settled_stat = settled_stat
    settled_stat = _wait_for_watcher_file_ready(file_path)
    if entrypoint_settled_stat is not None:
        _assert_watcher_file_unchanged(
            file_path=file_path,
            expected_stat=entrypoint_settled_stat,
            current_stat=settled_stat,
            stage="pre_hash_recheck",
        )
    file_hash = sha256_file(file_path)
    stat_result = file_path.stat()
    _assert_watcher_file_unchanged(
        file_path=file_path,
        expected_stat=settled_stat,
        current_stat=stat_result,
        stage="post_hash",
    )
    idempotency_key = (
        f"watcher:{file_hash}:{int(stat_result.st_mtime_ns)}:{stat_result.st_size}"
    )

    with file_path.open("rb") as handle:
        django_file = cast(_NamedUploadFile, File(handle, name=file_path.name))
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
    sidecar_paths = [
        candidate
        for suffix in (".json", ".yaml", ".yml")
        if (candidate := file_path.with_suffix(suffix)).exists()
    ]
    if len(sidecar_paths) > 1:
        raise ValueError(
            "Preanonymized input has multiple sidecars: "
            + ", ".join(watcher_path_reference_text(path) for path in sidecar_paths)
        )
    if not sidecar_paths:
        if strict:
            raise ValueError(
                "Preanonymized sidecar is required "
                "(.json, .yaml, or .yml); "
                f"{watcher_path_reference_text(file_path)}"
            )
        return None, None

    sidecar_path = sidecar_paths[0]
    sidecar_text = sidecar_path.read_text(encoding="utf-8")
    payload: object
    if sidecar_path.suffix == ".json":
        payload = json.loads(sidecar_text)
    else:
        payload = yaml.safe_load(sidecar_text)
    if not isinstance(payload, dict):
        raise ValueError(
            "Preanonymized sidecar must contain a mapping; "
            f"{watcher_path_reference_text(sidecar_path)}"
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
            "Invalid preanonymized sidecar payload; "
            f"{watcher_path_reference_text(sidecar_path)}"
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
        index_quarantine_file(
            quarantine_path,
            root=quarantine_dir,
            source_event="watcher.preanonymized_rejected",
            source_system=(
                upload_job.source_system if upload_job is not None else None
            ),
            reason="Preanonymized import validation failed.",
            upload_job=upload_job,
        )
    if sidecar_path is not None and sidecar_path.exists():
        quarantine_sidecar_path = quarantine_dir / sidecar_path.name
        if quarantine_sidecar_path.exists():
            quarantine_sidecar_path = (
                quarantine_dir / f"{uuid.uuid4().hex}_{sidecar_path.name}"
            )
        atomic_move_file(source=sidecar_path, destination=quarantine_sidecar_path)
        updates["quarantined_sidecar_path"] = str(quarantine_sidecar_path)
        index_quarantine_file(
            quarantine_sidecar_path,
            root=quarantine_dir,
            source_event="watcher.preanonymized_sidecar_rejected",
            source_system=(
                upload_job.source_system if upload_job is not None else None
            ),
            reason="Preanonymized sidecar validation failed.",
            upload_job=upload_job,
        )
    if upload_job is not None and updates:
        _update_upload_provenance(upload_job, **updates)
        upload_job.save(update_fields=["processing_provenance", "updated_at"])


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
    external_id_id = cast(int | None, getattr(sensitive_meta, "external_id_id", None))
    pseudo_patient_id = cast(
        int | None, getattr(sensitive_meta, "pseudo_patient_id", None)
    )
    if external_id_id != existing.pk:
        sensitive_meta.external_id = existing
        update_fields.append("external_id")
    canonical_patient_id = cast(int, existing.patient.pk)
    if pseudo_patient_id != canonical_patient_id:
        sensitive_meta.pseudo_patient = existing.patient
        update_fields.append("pseudo_patient")

    canonical_patient_hash = existing.patient.patient_hash
    if canonical_patient_hash and sensitive_meta.patient_hash != canonical_patient_hash:
        sensitive_meta.patient_hash = canonical_patient_hash
        update_fields.append("patient_hash")

    from endoreg_db.models.medical.patient.patient_examination import (
        PatientExamination,
    )

    examination_identity = "\0".join(
        (
            "preanonymized_external_case_v1",
            normalized_origin,
            normalized_external_id,
            sensitive_meta.casenumber or f"sensitive-meta:{sensitive_meta.pk}",
        )
    )
    canonical_examination_hash = hashlib.sha256(
        examination_identity.encode("utf-8")
    ).hexdigest()
    canonical_examination, _ = PatientExamination.objects.get_or_create(
        hash=canonical_examination_hash,
        defaults={
            "patient": existing.patient,
            "date_start": sensitive_meta.examination_date,
        },
    )
    if canonical_examination.patient_id != canonical_patient_id:
        raise IntegrityError(
            "The deterministic preanonymized examination identity is linked "
            "to a different patient."
        )
    pseudo_examination_id = cast(
        int | None, getattr(sensitive_meta, "pseudo_examination_id", None)
    )
    if pseudo_examination_id != canonical_examination.pk:
        sensitive_meta.pseudo_examination = canonical_examination
        update_fields.append("pseudo_examination")
    if sensitive_meta.examination_hash != canonical_examination_hash:
        sensitive_meta.examination_hash = canonical_examination_hash
        update_fields.append("examination_hash")
    if update_fields:
        # SensitiveMeta.save() intentionally recalculates demographic hashes and
        # pseudo links. External-ID reconciliation is the authoritative identity
        # operation here, so persist the selected fields without re-running that
        # derivation and keep the in-memory instance aligned with the update.
        SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
            **{
                field_name: getattr(sensitive_meta, field_name)
                for field_name in update_fields
            }
        )


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
    target_already_existed = final_path.exists()
    _persist_preanonymized_file(
        source_path=source_path,
        target_path=final_path,
        delete_source=False,
    )

    try:
        processor = _resolve_preanonymized_video_processor(processor_name)
        relative_name = to_storage_relative(final_path)
        with transaction.atomic():
            video = _get_or_update_preanonymized_video(
                source_path=source_path,
                center=center,
                processor=processor,
                video_hash=video_hash,
                relative_name=relative_name,
            )
            sensitive_meta = _apply_preanonymized_metadata(
                sensitive_meta=video.sensitive_meta,
                center=center,
                payload=payload,
            )
            _link_preanonymized_video_metadata(
                video=video,
                sensitive_meta=sensitive_meta,
            )
            _mark_preanonymized_video_ready(
                video=video,
                resolve_case=sensitive_meta is not None,
            )
    except Exception:
        if not target_already_existed:
            safe_unlink_file(final_path, missing_ok=True)
        raise

    if delete_source:
        safe_unlink_file(source_path, missing_ok=True)
    return video


def _resolve_preanonymized_video_processor(
    processor_name: str | None,
) -> EndoscopyProcessor | None:
    effective_processor_name = processor_name or _default_processor_name()
    if not effective_processor_name:
        return None
    return EndoscopyProcessor.objects.filter(name=effective_processor_name).first()


def _get_or_update_preanonymized_video(
    *,
    source_path: Path,
    center: Center,
    processor: EndoscopyProcessor | None,
    video_hash: str,
    relative_name: str,
) -> VideoFile:
    video = VideoFile.objects.filter(video_hash=video_hash).first()
    if video is None:
        return VideoFile.objects.create(
            center=center,
            processor=processor,
            original_file_name=source_path.name,
            video_hash=video_hash,
            processed_video_hash=video_hash,
            suffix=".mp4",
            processed_file=relative_name,
        )

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
    return video


def _link_preanonymized_video_metadata(
    *,
    video: VideoFile,
    sensitive_meta: SensitiveMeta | None,
) -> None:
    if sensitive_meta is None:
        return

    update_fields: list[str] = []
    sensitive_meta_patient_id = cast(
        int | None, getattr(sensitive_meta, "pseudo_patient_id", None)
    )
    sensitive_meta_examination_id = cast(
        int | None, getattr(sensitive_meta, "pseudo_examination_id", None)
    )
    if video.sensitive_meta_id != sensitive_meta.pk:
        video.sensitive_meta = sensitive_meta
        update_fields.append("sensitive_meta")
    if video.patient_id != sensitive_meta_patient_id:
        video.patient = sensitive_meta.pseudo_patient
        update_fields.append("patient")
    if video.examination_id != sensitive_meta_examination_id:
        video.examination = sensitive_meta.pseudo_examination
        update_fields.append("examination")
    if update_fields:
        video.save(update_fields=update_fields)


def _mark_preanonymized_video_ready(
    *,
    video: VideoFile,
    resolve_case: bool,
) -> None:
    state = get_or_create_video_state(video)
    state.mark_processing_started()
    state.mark_anonymized()
    state.mark_sensitive_meta_processed()
    state.mark_anonymization_validated()

    if resolve_case:
        try:
            auto_resolve_media_case(media_type="video", media_obj=video)
        except Exception as exc:
            logger.warning(
                "Preanonymized video case resolution failed for %s: %s",
                video.video_hash,
                exc,
            )
    sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=True,
        save=True,
    )


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
        sensitive_meta_patient_id = cast(
            int | None, getattr(sensitive_meta, "pseudo_patient_id", None)
        )
        sensitive_meta_examination_id = cast(
            int | None, getattr(sensitive_meta, "pseudo_examination_id", None)
        )
        if sensitive_meta is not None and report.sensitive_meta_id != sensitive_meta.pk:
            report.sensitive_meta = sensitive_meta
            update_fields.append("sensitive_meta")
        if (
            sensitive_meta is not None
            and report.patient_id != sensitive_meta_patient_id
        ):
            report.patient = sensitive_meta.pseudo_patient
            update_fields.append("patient")
        if (
            sensitive_meta is not None
            and report.examination_id != sensitive_meta_examination_id
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

        state = get_or_create_raw_pdf_state(report)
        state.mark_processing_started()
        state.mark_anonymized()
        state.mark_sensitive_meta_processed()
        # This watcher path has already hashed the exact immutable source bytes
        # before the atomic copy/move into the protected final directory.  The
        # legacy final path is not written through FieldFile.storage, so trying
        # to reopen it through LazyEncryptedStorage would interpret plaintext
        # PDF bytes as an encrypted container.  Persist the content-bound hash
        # from that verified handoff instead.
        state.processed_file_sha256 = pdf_hash
        state.save(update_fields=["processed_file_sha256", "date_modified"])
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
    upload_job_manager = UploadJob.objects
    job = upload_job_manager.select_related("source_center", "sensitive_meta").get(
        id=job_id
    )
    if job.status == UploadJob.Status.ANONYMIZED.value:
        return True

    if not job.file or not job.file.name:
        job.mark_lost("Upload job has no stored file")
        return False

    center = job.source_center
    if center is None:
        job.mark_error(
            "Upload job has no resolved source center",
            error_code=UploadJob.ErrorCode.INVALID_CONFIGURATION.value,
        )
        return False

    if job.content_type == "application/pdf":
        from endoreg_db.services.jobs.report_llm_jobs import dispatch_report_llm_import

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

    reserved_lease: UploadJobImportLease | None = None
    try:
        queue = queue_for_job_kind(HeavyJobKind.VIDEO_UPLOAD_IMPORT)
        task_id = uuid.uuid4().hex
        reserved_job, reserved_lease, should_dispatch = (
            _reserve_video_upload_import_handoff(
                upload_job_id=str(job.id),
                queue=queue,
                task_id=task_id,
            )
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
            if reserved_lease is None:
                raise RuntimeError(
                    "Video import dispatch lost its ownership lease before "
                    "recording the broker task identifier."
                )
            with locked_upload_job_import_lease(reserved_lease) as owned_job:
                _update_upload_provenance(
                    owned_job,
                    video_import_task_id=str(async_result.id),
                )
                owned_job.save(update_fields=["processing_provenance", "updated_at"])
        return True
    except Exception as exc:
        logger.exception("Video upload import handoff failed for %s: %s", job_id, exc)
        if reserved_lease is not None:
            try:
                with locked_upload_job_import_lease(reserved_lease) as owned_job:
                    if _is_celery_broker_connection_error(exc):
                        schedule_dispatch_retry(
                            owned_job,
                            technical_detail=f"Failed to start video import: {exc}",
                        )
                    else:
                        owned_job.mark_error(
                            f"Failed to start video import: {exc}",
                            error_code=UploadJob.ErrorCode.INVALID_CONFIGURATION.value,
                        )
                release_upload_job_import_lease(reserved_lease)
            except UploadJobImportLeaseLost:
                logger.warning(
                    "Video upload dispatch lease was already fenced: job=%s",
                    job_id,
                )
        elif _is_celery_broker_connection_error(exc):
            schedule_dispatch_retry(
                job,
                technical_detail=f"Failed to start video import: {exc}",
            )
        else:
            job.mark_error(
                f"Failed to start video import: {exc}",
                error_code=UploadJob.ErrorCode.INVALID_CONFIGURATION.value,
            )
        return False


@dataclass
class _VideoUploadImportAttempt:
    job_id: str
    job: UploadJob
    lease: UploadJobImportLease
    owner: str
    source_materialized: bool = False


def _acquire_video_upload_import_lease(
    *,
    job: UploadJob,
    job_id: str,
    owner: str,
) -> UploadJobImportLease | None:
    try:
        return acquire_upload_job_import_lease(
            upload_job_id=str(job.pk),
            owner=owner,
        )
    except UploadJobImportLeaseBusy:
        logger.info("Video upload job already has a live owner: job=%s", job_id)
        return None


def _validate_fenced_video_upload_source(
    lease: UploadJobImportLease,
) -> tuple[UploadJob, Center] | None:
    with locked_upload_job_import_lease(lease) as owned_job:
        if not owned_job.file or not owned_job.file.name:
            owned_job.mark_lost("Upload job has no stored file")
            return None
        if owned_job.source_center is None:
            owned_job.mark_error(
                "Upload job has no resolved source center",
                error_code=UploadJob.ErrorCode.INVALID_CONFIGURATION.value,
            )
            return None
        return owned_job, owned_job.source_center


def _mark_fenced_video_upload_processing(
    lease: UploadJobImportLease,
) -> tuple[UploadJob, UploadProvenance]:
    with locked_upload_job_import_lease(lease) as owned_job:
        owned_job.mark_processing()
        stored_upload_path = owned_job.file.name or ""
        provenance = _update_upload_provenance(
            owned_job,
            stored_upload_path=stored_upload_path,
            video_import_fencing_epoch=lease.fencing_epoch,
        )
        provenance.setdefault("stored_upload_path", stored_upload_path)
        owned_job.save(update_fields=["processing_provenance", "updated_at"])
        return owned_job, provenance


def _required_video_upload_processor_name(
    provenance: UploadProvenance,
) -> str:
    processor_name = provenance.get("processor_name") or _default_processor_name()
    if not processor_name:
        raise ObjectDoesNotExist("No default EndoscopyProcessor is configured")
    return processor_name


def _import_fenced_video_upload(
    *,
    attempt: _VideoUploadImportAttempt,
    heartbeat: UploadJobImportLeaseHeartbeat,
    file_path: Path,
    center: Center,
    provenance: UploadProvenance,
) -> VideoFile | None:
    """Adapt the wrapper-owned heartbeat into the video service capability."""
    processor_name = _required_video_upload_processor_name(provenance)
    from endoreg_db.import_files.video_import_service import VideoImportExecutionFence
    from endoreg_db.services.video_import import VideoImportService

    try:
        return VideoImportService().import_and_anonymize_fenced(
            file_path=file_path,
            center_name=center.name,
            processor_name=processor_name,
            retry=False,
            execution_fence=VideoImportExecutionFence(
                attempt_id=uuid.uuid5(uuid.NAMESPACE_URL, attempt.owner).hex,
                guard=heartbeat.guard,
            ),
        )
    except (IntegrityError, InsufficientStorageError):
        raise
    except Exception:
        heartbeat.guard()
        raise


def _complete_fenced_video_upload(
    *,
    lease: UploadJobImportLease,
    sensitive_meta: SensitiveMeta | None,
) -> UploadJob:
    with locked_upload_job_import_lease(lease) as owned_job:
        owned_job.mark_completed(sensitive_meta=sensitive_meta)
        return owned_job


def _dispatch_video_upload_prediction(
    *,
    job: UploadJob,
    job_id: str,
    video: VideoFile | None,
    provenance: UploadProvenance,
) -> None:
    prediction_model_name = provenance.get("prediction_model_name")
    if not isinstance(video, VideoFile) or not prediction_model_name:
        return
    try:
        from endoreg_db.services.video_temporal_inference import (
            dispatch_video_temporal_inference,
        )

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


def _execute_video_upload_import_attempt(
    attempt: _VideoUploadImportAttempt,
) -> bool:
    """Own the heartbeat lifecycle around one persisted UploadJob attempt.

    The heartbeat context renews the database lease in the background. The
    service receives only ``heartbeat.guard`` and cannot renew or extend its
    own authority.
    """
    validated_source = _validate_fenced_video_upload_source(attempt.lease)
    if validated_source is None:
        release_upload_job_import_lease(attempt.lease)
        return False
    attempt.job, center = validated_source

    with UploadJobImportLeaseHeartbeat(attempt.lease) as heartbeat:
        attempt.job, provenance = _mark_fenced_video_upload_processing(heartbeat.lease)
        with _ensure_upload_job_local_file(
            attempt.job,
            lease=heartbeat.lease,
        ) as file_path:
            attempt.source_materialized = True
            video = _import_fenced_video_upload(
                attempt=attempt,
                heartbeat=heartbeat,
                file_path=file_path,
                center=center,
                provenance=provenance,
            )
            sensitive_meta = (
                video.sensitive_meta if isinstance(video, VideoFile) else None
            )

        heartbeat.guard()
        attempt.job = _complete_fenced_video_upload(
            lease=heartbeat.lease,
            sensitive_meta=sensitive_meta,
        )
    release_upload_job_import_lease(attempt.lease)
    cleanup_upload_job_source(attempt.job)
    _dispatch_video_upload_prediction(
        job=attempt.job,
        job_id=attempt.job_id,
        video=video,
        provenance=provenance,
    )
    return True


def _mutate_fenced_failed_video_upload(
    *,
    attempt: _VideoUploadImportAttempt,
    mutation: Callable[[UploadJob], object],
    fenced_message: str,
) -> None:
    try:
        with locked_upload_job_import_lease(attempt.lease) as owned_job:
            mutation(owned_job)
        release_upload_job_import_lease(attempt.lease)
    except UploadJobImportLeaseLost:
        logger.error(fenced_message, attempt.job_id)


def _schedule_video_upload_storage_retry(
    attempt: _VideoUploadImportAttempt,
    exc: InsufficientStorageError,
) -> None:
    logger.warning(
        "Video upload job %s is waiting for pipeline storage: %s",
        attempt.job_id,
        exc,
    )
    _mutate_fenced_failed_video_upload(
        attempt=attempt,
        mutation=lambda owned_job: schedule_storage_retry(
            owned_job,
            technical_detail=str(exc),
        ),
        fenced_message="Storage-retry worker was fenced: job=%s",
    )


def _mark_duplicate_video_upload(
    attempt: _VideoUploadImportAttempt,
    exc: IntegrityError,
) -> None:
    logger.warning("Duplicate upload content rejected for job %s", attempt.job_id)
    _mutate_fenced_failed_video_upload(
        attempt=attempt,
        mutation=lambda owned_job: owned_job.mark_error(
            str(exc),
            error_code=UploadJob.ErrorCode.DUPLICATE_CONTENT.value,
        ),
        fenced_message="Duplicate-content worker was fenced: job=%s",
    )


def _schedule_video_upload_processing_retry(
    attempt: _VideoUploadImportAttempt,
    exc: BaseException,
) -> None:
    logger.exception(
        "Upload job processing failed for %s: %s",
        attempt.job_id,
        exc,
    )
    _mutate_fenced_failed_video_upload(
        attempt=attempt,
        mutation=lambda owned_job: schedule_processing_retry(
            owned_job,
            technical_detail=str(exc),
        ),
        fenced_message="Failed worker was fenced: job=%s",
    )


def _mark_video_upload_source_lost(
    attempt: _VideoUploadImportAttempt,
    exc: OSError,
) -> None:
    error_detail = f"Upload source could not be materialized from storage. {exc}"
    logger.exception(
        "Upload job source missing for %s: %s",
        attempt.job_id,
        exc,
    )
    _mutate_fenced_failed_video_upload(
        attempt=attempt,
        mutation=lambda owned_job: owned_job.mark_lost(error_detail),
        fenced_message="Missing-source worker was fenced: job=%s",
    )


def _handle_video_upload_os_error(
    attempt: _VideoUploadImportAttempt,
    exc: OSError,
) -> None:
    if attempt.source_materialized:
        _schedule_video_upload_processing_retry(attempt, exc)
        return
    _mark_video_upload_source_lost(attempt, exc)


def _handle_video_upload_import_failure(
    attempt: _VideoUploadImportAttempt,
    exc: Exception,
) -> bool:
    if isinstance(exc, UploadJobImportLeaseLost):
        logger.error(
            "Stale video upload worker fenced for %s: %s",
            attempt.job_id,
            exc,
        )
        return False
    if isinstance(exc, InsufficientStorageError):
        _schedule_video_upload_storage_retry(attempt, exc)
        return False
    if isinstance(exc, IntegrityError):
        _mark_duplicate_video_upload(attempt, exc)
        return False
    if isinstance(exc, OSError):
        _handle_video_upload_os_error(attempt, exc)
        return False
    _schedule_video_upload_processing_retry(attempt, exc)
    return False


def _run_video_upload_import_job(
    job_id: str,
    *,
    lease_owner: str | None = None,
) -> bool:
    job = UploadJob.objects.select_related("source_center", "sensitive_meta").get(
        id=job_id
    )
    if job.status == UploadJob.Status.ANONYMIZED.value:
        return True

    owner = (lease_owner or f"direct-{uuid.uuid4().hex}").strip()
    lease = _acquire_video_upload_import_lease(
        job=job,
        job_id=job_id,
        owner=owner,
    )
    if lease is None:
        return False

    attempt = _VideoUploadImportAttempt(
        job_id=job_id,
        job=job,
        lease=lease,
        owner=owner,
    )
    try:
        return _execute_video_upload_import_attempt(attempt)
    except Exception as exc:
        return _handle_video_upload_import_failure(attempt, exc)


def _run_watcher_upload_job_inline(
    *,
    upload_job: UploadJob,
    watched_path: Path,
    normalized_type: str,
    source_center: Center,
    processor_name: str | None = None,
) -> UploadJob:
    upload_job.refresh_from_db()
    upload_job.mark_processing()
    _update_upload_provenance(
        upload_job,
        processing_handoff="inline",
        watcher_processing_path=str(watched_path),
    )
    upload_job.save(
        update_fields=[
            "processing_provenance",
            "updated_at",
        ]
    )

    imported_media: RawPdfFile | VideoFile | None = None
    sensitive_meta: SensitiveMeta | None = None
    if normalized_type == "report":
        from endoreg_db.services.report_import import ReportImportService

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
        from endoreg_db.services.video_import import VideoImportService

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
            from endoreg_db.services.video_temporal_inference import (
                dispatch_video_temporal_inference,
            )

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
    task_dispatcher: CeleryTaskDispatcher | DelayTaskDispatcher | None = None,
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
    upload_job.processing_provenance = cast(JsonObject, provenance)
    handoff_mode = "celery" if task_dispatcher is not None else "inline"

    try:
        if task_dispatcher is not None:
            queue = queue_for_job_kind(HeavyJobKind.PIPELINE_INGEST)
            ensure_secure_transport_for_job_kind(HeavyJobKind.PIPELINE_INGEST)
            apply_async = getattr(task_dispatcher, "apply_async", None)
            if callable(apply_async):
                apply_async(
                    args=(str(upload_job.id),),
                    queue=queue,
                    routing_key=queue,
                )
            else:
                delay = getattr(task_dispatcher, "delay", None)
                if not callable(delay):
                    raise TypeError(
                        "Task dispatcher must provide apply_async or delay."
                    )
                delay(str(upload_job.id))
        else:
            processed = process_upload_job(str(upload_job.id))
            if not processed:
                refreshed_job = UploadJob.objects.filter(id=upload_job.id).first()
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
        upload_job.refresh_from_db()
        if upload_job.status != UploadJob.Status.RETRYING.value:
            if _is_celery_broker_connection_error(exc):
                schedule_dispatch_retry(
                    upload_job,
                    technical_detail=f"Failed to start processing: {exc}",
                )
            else:
                upload_job.mark_error(
                    f"Failed to start processing: {exc}",
                    error_code=UploadJob.ErrorCode.INVALID_CONFIGURATION.value,
                )
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
        raise FileNotFoundError(
            f"Watcher file not found; {watcher_path_reference_text(watched_path)}"
        )
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

    normalized_type, content_type = _watcher_file_contract(file_type)
    settled_stat = _wait_for_watcher_file_ready(watched_path)
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
        settled_stat=settled_stat,
    )
    reused_job = _reuse_watcher_upload_job(
        upload_job=upload_job,
        created=created,
        watched_path=watched_path,
    )
    if reused_job is not None:
        return reused_job
    _mark_watcher_upload_job_processing(
        upload_job=upload_job,
        watched_path=watched_path,
    )
    effective_processor_name = processor_name or _default_processor_name()

    try:
        _prepare_watcher_video_dispatch(
            upload_job=upload_job,
            watched_path=watched_path,
            normalized_type=normalized_type,
            effective_processor_name=effective_processor_name,
        )
        start_upload_job_processing(
            upload_job=upload_job,
            task_dispatcher=_upload_job_task_dispatcher(),
        )
        safe_unlink_file(watched_path, missing_ok=True)
        return upload_job
    except Exception as exc:
        recovered_job = _handle_watcher_handoff_failure(
            upload_job=upload_job,
            watched_path=watched_path,
            normalized_type=normalized_type,
            source_center=source_center,
            effective_processor_name=effective_processor_name,
            exc=exc,
        )
        if recovered_job is not None:
            return recovered_job
        raise


def _watcher_file_contract(file_type: str) -> tuple[str, str]:
    normalized_type = file_type.strip().lower()
    if normalized_type == "report":
        return normalized_type, "application/pdf"
    if normalized_type == "video":
        return normalized_type, "video/mp4"
    raise ValueError(f"Unsupported watcher file type: {file_type}")


def _reuse_watcher_upload_job(
    *,
    upload_job: UploadJob,
    created: bool,
    watched_path: Path,
) -> UploadJob | None:
    if not created:
        safe_unlink_file(watched_path, missing_ok=True)
        return upload_job
    if not upload_job.is_complete:
        return None
    if _upload_job_has_usable_media(upload_job):
        safe_unlink_file(watched_path, missing_ok=True)
        return upload_job

    upload_job.mark_error(
        "Upload job marked complete but no usable media artifact was found. Forcing re-ingest."
    )
    return None


def _mark_watcher_upload_job_processing(
    *,
    upload_job: UploadJob,
    watched_path: Path,
) -> None:
    upload_job.mark_processing()
    _ = _update_upload_provenance(
        upload_job,
        watcher_processing_path=str(watched_path),
    )
    upload_job.save(update_fields=["processing_provenance", "updated_at"])


def _prepare_watcher_video_dispatch(
    *,
    upload_job: UploadJob,
    watched_path: Path,
    normalized_type: str,
    effective_processor_name: str | None,
) -> None:
    if normalized_type != "video":
        return
    if not effective_processor_name:
        raise ObjectDoesNotExist("No default EndoscopyProcessor is configured")
    _ = _update_upload_provenance(
        upload_job,
        watcher_processing_path=str(watched_path),
        processor_name=effective_processor_name,
    )
    upload_job.save(update_fields=["processing_provenance", "updated_at"])


def _handle_watcher_handoff_failure(
    *,
    upload_job: UploadJob,
    watched_path: Path,
    normalized_type: str,
    source_center: Center,
    effective_processor_name: str | None,
    exc: Exception,
) -> UploadJob | None:
    technical_error = exc
    broker_error = _is_celery_broker_connection_error(exc)
    inline_fallback_enabled = bool(
        getattr(settings, "WATCHER_CELERY_INLINE_FALLBACK_ENABLED", False)
    )
    if broker_error and inline_fallback_enabled:
        emit_structured_event(
            logger,
            "watcher.celery_handoff_failed_inline",
            level=logging.WARNING,
            file=path_reference(watched_path),
            error=safe_log_value(exc, key="error"),
        )
        try:
            return _run_watcher_upload_job_inline(
                upload_job=upload_job,
                watched_path=watched_path,
                normalized_type=normalized_type,
                source_center=source_center,
                processor_name=(
                    effective_processor_name if normalized_type == "video" else None
                ),
            )
        except Exception as inline_exc:
            technical_error = inline_exc
    elif broker_error:
        emit_structured_event(
            logger,
            "watcher.celery_handoff_failed",
            level=logging.WARNING,
            file=path_reference(watched_path),
            inline_fallback_enabled=False,
            error=safe_log_value(exc, key="error"),
        )

    emit_structured_event(
        logger,
        "watcher.processing_handoff_failed",
        level=logging.ERROR,
        file=path_reference(watched_path),
        error=safe_log_value(technical_error, key="error"),
    )
    upload_job.refresh_from_db()
    if upload_job.status == UploadJob.Status.RETRYING.value:
        safe_unlink_file(watched_path, missing_ok=True)
        return upload_job
    schedule_processing_retry(upload_job, technical_detail=str(technical_error))
    safe_unlink_file(watched_path, missing_ok=True)
    return None


@dataclass(frozen=True)
class _PreanonymizedWatcherContext:
    normalized_type: str
    content_type: str
    source_center: Center
    metadata_payload: PreanonymizedIngestPayload | None
    sidecar_path: Path


def _preanonymized_file_contract(watched_path: Path) -> tuple[str, str]:
    contracts = {
        ".pdf": ("report", "application/pdf"),
        ".mp4": ("video", "video/mp4"),
        ".txt": ("report", "export/txt"),
    }
    try:
        return contracts[watched_path.suffix.lower()]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported preanonymized watcher file suffix: {watched_path.suffix}"
        ) from exc


def _verify_strict_preanonymized_source(
    *,
    watched_path: Path,
    settled_stat: os.stat_result,
    payload: PreanonymizedIngestPayload,
) -> None:
    _validate_local_preanonymized_drop_path(watched_path)
    declared_hash = (payload.file_sha256 or "").strip().lower()
    actual_hash = sha256_file(watched_path)
    _assert_watcher_file_unchanged(
        file_path=watched_path,
        expected_stat=settled_stat,
        current_stat=watched_path.stat(),
        stage="preanonymized_sidecar_hash",
    )
    if declared_hash != actual_hash:
        raise ValueError("Preanonymized sidecar file_sha256 does not match media file")


def _resolve_strict_preanonymized_center(
    *,
    payload: PreanonymizedIngestPayload,
    watcher_center: Center | None,
) -> Center:
    source_center, center_error = resolve_declared_upload_center(
        center_key=payload.center_key,
        center_name=None,
    )
    if center_error:
        raise ValueError(center_error)
    if source_center is None:
        raise ObjectDoesNotExist(
            "No center is configured for preanonymized watcher ingestion"
        )
    if watcher_center is not None and watcher_center.pk != source_center.pk:
        raise ValueError("Declared sidecar center_key does not match watcher center")
    return source_center


def _resolve_optional_preanonymized_center(
    *,
    payload: PreanonymizedIngestPayload | None,
    watcher_center: Center | None,
) -> Center:
    declared_center = _resolve_sidecar_center(payload)
    source_center = _watcher_center_or_default(
        watcher_center=watcher_center,
        declared_center=declared_center,
    )
    if source_center is None:
        raise ObjectDoesNotExist("No center is configured for watcher ingestion")
    return source_center


def _resolve_sidecar_center(
    payload: PreanonymizedIngestPayload | None,
) -> Center | None:
    if payload is None:
        return None
    declared_center, center_error = resolve_declared_upload_center(
        center_key=payload.center_key,
        center_name=payload.center_name,
    )
    if center_error:
        raise ValueError(center_error)
    return declared_center


def _watcher_center_or_default(
    *,
    watcher_center: Center | None,
    declared_center: Center | None,
) -> Center | None:
    if watcher_center is not None:
        return watcher_center
    if declared_center is not None:
        return declared_center
    return resolve_default_center()


def _load_preanonymized_watcher_context(
    *,
    watched_path: Path,
    settled_stat: os.stat_result,
    center: Center | None,
) -> _PreanonymizedWatcherContext:
    normalized_type, content_type = _preanonymized_file_contract(watched_path)
    strict_local = local_study_server_mode_enabled()
    metadata_payload, loaded_sidecar_path = _load_preanonymized_sidecar(
        watched_path,
        strict=strict_local,
    )
    sidecar_path = loaded_sidecar_path or watched_path.with_suffix(".json")
    if strict_local:
        assert metadata_payload is not None
        _verify_strict_preanonymized_source(
            watched_path=watched_path,
            settled_stat=settled_stat,
            payload=metadata_payload,
        )
        source_center = _resolve_strict_preanonymized_center(
            payload=metadata_payload,
            watcher_center=center,
        )
    else:
        source_center = _resolve_optional_preanonymized_center(
            payload=metadata_payload,
            watcher_center=center,
        )
    return _PreanonymizedWatcherContext(
        normalized_type=normalized_type,
        content_type=content_type,
        source_center=source_center,
        metadata_payload=metadata_payload,
        sidecar_path=sidecar_path,
    )


def _prepare_preanonymized_watcher_context(
    *,
    watched_path: Path,
    settled_stat: os.stat_result,
    center: Center | None,
    source_system: str,
) -> _PreanonymizedWatcherContext:
    sidecar_path = next(
        (
            candidate
            for suffix in (".json", ".yaml", ".yml")
            if (candidate := watched_path.with_suffix(suffix)).exists()
        ),
        watched_path.with_suffix(".json"),
    )
    try:
        return _load_preanonymized_watcher_context(
            watched_path=watched_path,
            settled_stat=settled_stat,
            center=center,
        )
    except WatcherFileNotReadyError:
        raise
    except Exception as exc:
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


def _discard_preanonymized_drop(
    *,
    watched_path: Path,
    sidecar_path: Path,
) -> None:
    safe_unlink_file(watched_path, missing_ok=True)
    safe_unlink_file(sidecar_path, missing_ok=True)


def _reuse_preanonymized_upload_job(
    *,
    upload_job: UploadJob,
    created: bool,
    watched_path: Path,
    sidecar_path: Path,
) -> UploadJob | None:
    if not created:
        _discard_preanonymized_drop(
            watched_path=watched_path,
            sidecar_path=sidecar_path,
        )
        return upload_job
    if not upload_job.is_complete:
        return None
    if _upload_job_has_usable_media(upload_job):
        _discard_preanonymized_drop(
            watched_path=watched_path,
            sidecar_path=sidecar_path,
        )
        return upload_job
    upload_job.mark_error(
        "Upload job marked complete but no usable media artifact was found. Forcing re-ingest."
    )
    return None


def _finalize_preanonymized_watcher_media(
    *,
    context: _PreanonymizedWatcherContext,
    watched_path: Path,
    upload_job: UploadJob,
    processor_name: str | None,
) -> SensitiveMeta | None:
    if context.normalized_type == "report":
        report = _finalize_preanonymized_report(
            source_path=watched_path,
            center=context.source_center,
            payload=context.metadata_payload,
            delete_source=True,
        )
        return report.sensitive_meta

    video = _finalize_preanonymized_video(
        source_path=watched_path,
        center=context.source_center,
        processor_name=processor_name,
        payload=context.metadata_payload,
        delete_source=True,
    )
    _update_upload_provenance(
        upload_job,
        watcher_processing_path=str(watched_path),
        processor_name=processor_name or _default_processor_name(),
    )
    return video.sensitive_meta


def _complete_preanonymized_watcher_job(
    *,
    upload_job: UploadJob,
    sensitive_meta: SensitiveMeta | None,
    context: _PreanonymizedWatcherContext,
    watched_path: Path,
    source_system: str,
) -> UploadJob:
    safe_unlink_file(context.sidecar_path, missing_ok=True)
    upload_job.save(update_fields=["processing_provenance", "updated_at"])
    upload_job.mark_completed(sensitive_meta=sensitive_meta)
    cleanup_upload_job_source(upload_job)
    emit_hub_audit_event(
        "hub.preanonymized_drop_accepted",
        upload_job_id=str(upload_job.id),
        source_system=source_system,
        request_user=None,
        center_key=context.source_center.center_key,
        watched_path=str(watched_path),
        sidecar_path=str(context.sidecar_path),
        content_hash=upload_job.content_hash,
    )
    return upload_job


def _quarantine_failed_preanonymized_media(
    *,
    upload_job: UploadJob,
    watched_path: Path,
) -> None:
    try:
        quarantine_path = _quarantine_dir() / watched_path.name
        atomic_move_file(source=watched_path, destination=quarantine_path)
        _update_upload_provenance(upload_job, quarantined_path=str(quarantine_path))
        upload_job.save(update_fields=["processing_provenance"])
        emit_structured_event(
            logger,
            "watcher.quarantine_media_moved",
            level=logging.WARNING,
            source=path_reference(watched_path),
            destination=path_reference(quarantine_path),
        )
    except Exception as move_exc:
        emit_structured_event(
            logger,
            "watcher.quarantine_media_move_failed",
            level=logging.ERROR,
            source=path_reference(watched_path),
            error=safe_log_value(move_exc, key="error"),
        )


def _quarantine_failed_preanonymized_sidecar(
    *,
    upload_job: UploadJob,
    sidecar_path: Path,
) -> None:
    if not sidecar_path.exists():
        return
    try:
        quarantine_sidecar_path = _quarantine_dir() / sidecar_path.name
        atomic_move_file(
            source=sidecar_path,
            destination=quarantine_sidecar_path,
        )
        _update_upload_provenance(
            upload_job,
            quarantined_sidecar_path=str(quarantine_sidecar_path),
        )
        upload_job.save(update_fields=["processing_provenance", "updated_at"])
        emit_structured_event(
            logger,
            "watcher.quarantine_sidecar_moved",
            level=logging.WARNING,
            source=path_reference(sidecar_path),
            destination=path_reference(quarantine_sidecar_path),
        )
    except Exception as move_exc:
        emit_structured_event(
            logger,
            "watcher.quarantine_sidecar_move_failed",
            level=logging.ERROR,
            source=path_reference(sidecar_path),
            error=safe_log_value(move_exc, key="error"),
        )


def _handle_preanonymized_watcher_failure(
    *,
    upload_job: UploadJob,
    context: _PreanonymizedWatcherContext,
    watched_path: Path,
    source_system: str,
    exc: Exception,
) -> None:
    emit_structured_event(
        logger,
        "watcher.preanonymized_processing_failed",
        level=logging.ERROR,
        file=path_reference(watched_path),
        error=safe_log_value(exc, key="error"),
    )
    upload_job.mark_error(str(exc))
    emit_hub_audit_event(
        "hub.preanonymized_drop_rejected",
        upload_job_id=str(upload_job.id),
        source_system=source_system,
        request_user=None,
        center_key=context.source_center.center_key,
        watched_path=str(watched_path),
        sidecar_path=str(context.sidecar_path),
        reason=str(exc),
    )
    _quarantine_failed_preanonymized_media(
        upload_job=upload_job,
        watched_path=watched_path,
    )
    _quarantine_failed_preanonymized_sidecar(
        upload_job=upload_job,
        sidecar_path=context.sidecar_path,
    )
    _cleanup_persisted_watcher_source(upload_job)


def _settled_watcher_path(file_path: Path | str) -> tuple[Path, os.stat_result]:
    watched_path = Path(file_path)
    if not watched_path.exists():
        raise FileNotFoundError(
            f"Watcher file not found; {watcher_path_reference_text(watched_path)}"
        )
    return watched_path, _wait_for_watcher_file_ready(watched_path)


def _emit_preanonymized_center_resolved(
    *,
    context: _PreanonymizedWatcherContext,
    watcher_center: Center | None,
    source_system: str,
) -> None:
    explicitly_declared = watcher_center is not None
    emit_hub_audit_event(
        "hub.center_resolved",
        source_system=source_system,
        request_user=None,
        hub_mode=hub_mode_enabled(),
        declared_center_key=(
            context.source_center.center_key if explicitly_declared else None
        ),
        declared_center_name=(
            context.source_center.name if explicitly_declared else None
        ),
        resolved_center_key=context.source_center.center_key,
        allowed_center_id=None,
    )


def _preanonymized_processing_provenance(
    context: _PreanonymizedWatcherContext,
) -> UploadProvenance:
    sidecar_payload = (
        context.metadata_payload.model_dump(mode="json", exclude_none=True)
        if context.metadata_payload is not None
        else {}
    )
    return {
        "file_type": context.normalized_type,
        "ingest_variant": "preanonymized",
        "sidecar_path": str(context.sidecar_path),
        "sidecar_payload": sidecar_payload,
    }


def _create_preanonymized_upload_job(
    *,
    watched_path: Path,
    settled_stat: os.stat_result,
    context: _PreanonymizedWatcherContext,
    source_system: str,
) -> tuple[UploadJob, bool]:
    return create_or_reuse_watcher_upload_job(
        file_path=watched_path,
        content_type=context.content_type,
        source_center=context.source_center,
        source_system=source_system,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        settled_stat=settled_stat,
        processing_provenance=_preanonymized_processing_provenance(context),
    )


def _mark_preanonymized_job_processing(
    *,
    upload_job: UploadJob,
    watched_path: Path,
) -> None:
    upload_job.mark_processing()
    _update_upload_provenance(
        upload_job,
        watcher_processing_path=str(watched_path),
    )
    upload_job.save(update_fields=["processing_provenance", "updated_at"])


def process_preanonymized_watcher_file(
    *,
    file_path: Path | str,
    center: Center | None = None,
    processor_name: str | None = None,
    source_system: str = "watcher_preanonymized",
) -> UploadJob:
    watched_path, settled_stat = _settled_watcher_path(file_path)
    _opportunistic_reap_watcher_sources()
    context = _prepare_preanonymized_watcher_context(
        watched_path=watched_path,
        settled_stat=settled_stat,
        center=center,
        source_system=source_system,
    )

    _emit_preanonymized_center_resolved(
        context=context,
        watcher_center=center,
        source_system=source_system,
    )

    upload_job, created = _create_preanonymized_upload_job(
        watched_path=watched_path,
        settled_stat=settled_stat,
        context=context,
        source_system=source_system,
    )
    reused_job = _reuse_preanonymized_upload_job(
        upload_job=upload_job,
        created=created,
        watched_path=watched_path,
        sidecar_path=context.sidecar_path,
    )
    if reused_job is not None:
        return reused_job

    _mark_preanonymized_job_processing(
        upload_job=upload_job,
        watched_path=watched_path,
    )

    try:
        sensitive_meta = _finalize_preanonymized_watcher_media(
            context=context,
            watched_path=watched_path,
            upload_job=upload_job,
            processor_name=processor_name,
        )
        return _complete_preanonymized_watcher_job(
            upload_job=upload_job,
            sensitive_meta=sensitive_meta,
            context=context,
            watched_path=watched_path,
            source_system=source_system,
        )
    except Exception as exc:
        _handle_preanonymized_watcher_failure(
            upload_job=upload_job,
            context=context,
            watched_path=watched_path,
            source_system=source_system,
            exc=exc,
        )
        raise
