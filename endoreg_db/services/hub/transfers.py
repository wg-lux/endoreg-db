# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Literal, NotRequired, TypedDict, cast

from django.core.files import File
from django.db.models.fields.files import FieldFile
from django.core.files.uploadedfile import UploadedFile
from django.db import (
    DatabaseError,
    IntegrityError,
    close_old_connections,
    connection,
    models,
    transaction,
)
from django.db.models import QuerySet
from django.db.models.functions import Now
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue
from lx_dtypes.models.contracts.hub_media_envelope import (
    HubMediaEnvelopeReceipt,
    validate_hub_media_receipt_matches_envelope,
)

from endoreg_db.services.media.state import update_media_state
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.network_node import NetworkNode
from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
    suppress_label_video_segment_state_side_effects,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.information_source import (
    InformationSource,
    InformationSourceManager,
)
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.models.state.label_video_segment import LabelVideoSegmentState
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.metadata.video_prediction_meta import VideoPredictionMeta
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.cases.auto_resolution import auto_resolve_media_case
from endoreg_db.services.media.operation_gate import (
    video_has_active_media_operation_leases,
)
from endoreg_db.schemas.hub_payloads import TransferPublicationPredecessor
from endoreg_db.services.runtime.status_writers import (
    set_case_resolution_status,
    set_patient_examination_report_status,
    set_transfer_status,
    update_transfer_status,
)
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.services.hub.cleanup import stored_media_name_referenced
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.video_files.state import get_or_create_video_state
from endoreg_db.utils.video.timeline import (
    presentation_timestamp_seconds,
    validate_presentation_timestamp_seconds,
)
from endoreg_db.services.video_storage.frame_provenance import resolve_video_provenance
from endoreg_db.utils.file_operations import (
    safe_delete_field_file,
)
from endoreg_db.utils.encryption.encrypted import EncryptedStorage, LazyEncryptedStorage
from endoreg_db.services.hub.payloads import reject_transferred_identity_hashes
from endoreg_db.utils.hashs import get_file_hash
from lx_dtypes.models.contracts.hub_transfer import (
    HubTransferSensitiveMetaPayload,
    HubTransferVideoSegmentPayload,
)
from lx_dtypes.models.contracts.video_provenance import (
    VideoFrameTimeline,
    VideoProvenanceBundle,
    VideoProvenanceRecovery,
)
from endoreg_db.services.video_files.segment_coordinates import (
    SegmentFrameRange,
    segment_timeline_coordinates,
    wire_timeline_coordinates,
)
from endoreg_db.utils.structured_logging import hash_identifier
from .hub_record_identity import (
    assert_examination_matches_identity,
    assert_existing_meta_matches_identity,
    assert_patient_owned_by_source,
    derive_hub_record_identity_hashes,
)
from .transfer_envelope import (
    HubMediaEnvelopeReplayConflict,
    prepare_inbound_hub_envelope,
)

logger = logging.getLogger(__name__)

TRANSFER_OPERATION_LEASE_SECONDS = 30 * 60


@dataclass(frozen=True)
class TransferOperationFence:
    transfer_job_id: uuid.UUID
    attempt_id: uuid.UUID
    owner_id: str
    fencing_token: int
    orphaned_candidate_name: str = ""


class TransferOperationBusy(RuntimeError):
    pass


def _transfer_database_now(transfer_job_id: uuid.UUID) -> datetime:
    value = (
        TransferJob.objects.filter(pk=transfer_job_id)
        .annotate(database_now=Now())
        .values_list("database_now", flat=True)
        .get()
    )
    if not isinstance(value, datetime):
        raise RuntimeError("database did not return a transfer lease timestamp")
    return value


def _claim_transfer_operation(
    transfer_job_id: uuid.UUID,
    *,
    lease_seconds: int = TRANSFER_OPERATION_LEASE_SECONDS,
) -> tuple[TransferJob, TransferOperationFence]:
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    owner_id = uuid.uuid4().hex
    attempt_id = uuid.uuid4()
    with transaction.atomic():
        transfer_job = TransferJob.objects.select_for_update().get(pk=transfer_job_id)
        if (
            transfer_job.transfer_status
            == TransferJob.TransferStatus.INCONSISTENT.value
        ):
            raise ValueError(
                "inconsistent transfer cannot be reclaimed without explicit repair"
            )
        now = _transfer_database_now(transfer_job_id)
        orphaned_candidate_name = transfer_job.operation_candidate_name
        if (
            transfer_job.transfer_status == TransferJob.TransferStatus.RUNNING.value
            and transfer_job.operation_lease_expires_at is not None
            and transfer_job.operation_lease_expires_at > now
        ):
            raise TransferOperationBusy("transfer operation already has a live owner")
        # The native hub_inbound_transfer table decides which statuses may claim.
        with transaction.atomic():
            set_transfer_status(transfer_job, TransferJob.TransferStatus.RUNNING)
            transfer_job.attempt_id = attempt_id
            transfer_job.operation_owner = owner_id
            transfer_job.operation_fencing_token += 1
            transfer_job.operation_heartbeat_at = now
            transfer_job.operation_lease_expires_at = now + timedelta(
                seconds=lease_seconds
            )
            transfer_job.operation_candidate_name = orphaned_candidate_name
            transfer_job.save(
                update_fields=[
                    "transfer_status",
                    "attempt_id",
                    "operation_owner",
                    "operation_fencing_token",
                    "operation_heartbeat_at",
                    "operation_lease_expires_at",
                    "operation_candidate_name",
                    "updated_at",
                ]
            )
        return transfer_job, TransferOperationFence(
            transfer_job_id=transfer_job_id,
            attempt_id=attempt_id,
            owner_id=owner_id,
            fencing_token=int(transfer_job.operation_fencing_token),
            orphaned_candidate_name=orphaned_candidate_name,
        )


def _renew_transfer_operation(fence: TransferOperationFence) -> None:
    now = _transfer_database_now(fence.transfer_job_id)
    updated = TransferJob.objects.filter(
        pk=fence.transfer_job_id,
        transfer_status=TransferJob.TransferStatus.RUNNING,
        attempt_id=fence.attempt_id,
        operation_owner=fence.owner_id,
        operation_fencing_token=fence.fencing_token,
        operation_lease_expires_at__gt=now,
    ).update(
        operation_heartbeat_at=now,
        operation_lease_expires_at=now
        + timedelta(seconds=TRANSFER_OPERATION_LEASE_SECONDS),
        updated_at=Now(),
    )
    if updated != 1:
        raise RuntimeError("transfer operation ownership fence is no longer current")


def _record_transfer_candidate(
    fence: TransferOperationFence,
    *,
    candidate_name: str,
) -> None:
    updated = TransferJob.objects.filter(
        pk=fence.transfer_job_id,
        transfer_status=TransferJob.TransferStatus.RUNNING,
        attempt_id=fence.attempt_id,
        operation_owner=fence.owner_id,
        operation_fencing_token=fence.fencing_token,
        operation_lease_expires_at__gt=Now(),
    ).update(operation_candidate_name=candidate_name, updated_at=Now())
    if updated != 1:
        raise RuntimeError("transfer candidate rejected by ownership fence")


class TransferOperationHeartbeat:
    def __init__(self, fence: TransferOperationFence) -> None:
        self._fence = fence
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=65)

    def guard(self) -> None:
        if self._failure is not None:
            raise RuntimeError("transfer operation heartbeat failed") from self._failure
        _renew_transfer_operation(self._fence)

    def _run(self) -> None:
        close_old_connections()
        try:
            while not self._stop.wait(60):
                _renew_transfer_operation(self._fence)
        except BaseException as exc:
            self._failure = exc
            self._stop.set()
        finally:
            close_old_connections()


_UNSAFE_STRUCTURED_REPORT_FIELDS = frozenset(
    {
        "title",
        "editor_payload",
        "patient_context_snapshot",
        "history_context_snapshot",
        "rendered_text",
        "created_by",
        "updated_by",
        "finalized_by",
    }
)


def _is_transfer_key_unique_violation(error: IntegrityError) -> bool:
    """Recognize only the database constraint protecting ``transfer_key``."""

    table_name = TransferJob._meta.db_table
    field = cast(
        models.Field[Any, Any],
        TransferJob._meta.get_field("transfer_key"),
    )
    column_name = field.column
    cause = error.__cause__
    diagnostic = getattr(cause, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)

    if isinstance(constraint_name, str) and constraint_name:
        try:
            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(
                    cursor,
                    table_name,
                )
        except DatabaseError:
            return False
        transfer_key_constraints = {
            name
            for name, details in constraints.items()
            if details.get("unique") is True and details.get("columns") == [column_name]
        }
        return constraint_name in transfer_key_constraints

    return False


class TransferProvenance(TypedDict, total=False):
    entrypoint: str
    source_node_key: str
    target_node_key: str
    source_center_key: str | None
    transfer_mode: str
    processing_policy: str
    cleanup_policy: str
    media_uploads: list[dict[str, JsonValue]]
    publication_predecessor: JsonObject
    case_resolution: dict[str, JsonValue]
    custom_marker: NotRequired[str]


def _transfer_provenance(
    existing: JsonObject | TransferProvenance | None = None,
) -> TransferProvenance:
    provenance: TransferProvenance = {}
    if existing:
        provenance.update(cast(TransferProvenance, dict(existing)))
    return provenance


def _json_object(value: JsonValue | None, *, field_name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return cast(JsonObject, value)


def _json_object_list(
    value: JsonValue | None,
    *,
    field_name: str,
) -> list[JsonObject]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON list")
    items: list[JsonObject] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{index}] must be a JSON object")
        items.append(cast(JsonObject, item))
    return items


def _assert_privacy_preserving_resource_rows(resource_rows: JsonObject) -> None:
    reject_transferred_identity_hashes(resource_rows)
    sensitive_meta = resource_rows.get("sensitive_meta")
    if isinstance(sensitive_meta, dict):
        forbidden = sorted(
            set(sensitive_meta).difference({"patient_uuid", "examination_uuid"})
        )
        if forbidden:
            raise ValueError(
                "Direct identity fields are prohibited in hub transfers: "
                + ", ".join(forbidden)
            )

    raw_pdf_file = resource_rows.get("raw_pdf_file")
    if isinstance(raw_pdf_file, dict) and "text" in raw_pdf_file:
        raise ValueError(
            "Raw report text is prohibited in hub transfers; only validated "
            "anonymized_text is accepted."
        )

    video_file = resource_rows.get("video_file")
    if isinstance(video_file, dict):
        forbidden_video_fields = sorted(
            set(video_file).intersection({"original_file_name", "meta"})
        )
        if forbidden_video_fields:
            raise ValueError(
                "Unsafe video metadata fields are prohibited in hub transfers: "
                + ", ".join(forbidden_video_fields)
            )

    for row in _json_object_list(
        resource_rows.get("reports"), field_name="resource_rows.reports"
    ):
        forbidden_report_fields = sorted(
            set(row).intersection(_UNSAFE_STRUCTURED_REPORT_FIELDS)
        )
        if forbidden_report_fields:
            raise ValueError(
                "Unsafe structured report fields are prohibited in hub transfers: "
                + ", ".join(forbidden_report_fields)
            )


def _json_int(
    value: JsonValue | None,
    *,
    field_name: str,
    default: int | None = None,
) -> int:
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _json_float(
    value: JsonValue | None,
    *,
    field_name: str,
) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number")
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"{field_name} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc


def _json_bool(value: JsonValue | None, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def _update_transfer_provenance(
    transfer_job: TransferJob,
    **updates: object,
) -> TransferProvenance:
    provenance = _transfer_provenance(transfer_job.provenance)
    for key, value in updates.items():
        if value is not None:
            cast(Any, provenance)[key] = value
    transfer_job.provenance = cast(JsonObject, provenance)
    return provenance


def _normalized_transfer_provenance(
    *,
    provenance: TransferProvenance,
    source_node: NetworkNode,
    target_node: NetworkNode,
    source_center: Center | None,
    transfer_mode: str,
    processing_policy: str,
    cleanup_policy: str,
) -> TransferProvenance:
    normalized = _transfer_provenance(provenance)
    normalized.setdefault("entrypoint", "transfer")
    normalized["source_node_key"] = source_node.node_key
    normalized["target_node_key"] = target_node.node_key
    normalized["source_center_key"] = (
        source_center.center_key if source_center is not None else None
    )
    normalized["transfer_mode"] = transfer_mode
    normalized["processing_policy"] = processing_policy
    normalized["cleanup_policy"] = cleanup_policy
    return normalized


def _publication_predecessor(
    target: VideoFile | RawPdfFile,
) -> TransferPublicationPredecessor:
    processed_hash = (
        target.processed_video_hash
        if isinstance(target, VideoFile)
        else target.state.processed_file_sha256
        if target.state is not None
        else ""
    )
    return TransferPublicationPredecessor(
        processed_file_name=str(target.processed_file.name or ""),
        processed_content_hash=str(processed_hash or ""),
    )


def _require_transfer_publication_predecessor(
    transfer_job: TransferJob, target: VideoFile | RawPdfFile
) -> None:
    current = _publication_predecessor(target)
    previous = transfer_job.provenance.get("publication_predecessor")
    if previous is None:
        # Older pending registrations may initialize proof only for an empty target.
        if current.processed_file_name or current.processed_content_hash:
            raise ValueError(
                "Receiver publication predecessor is missing for existing media"
            )
        _update_transfer_provenance(
            transfer_job, publication_predecessor=current.model_dump(mode="json")
        )
    elif TransferPublicationPredecessor.model_validate(previous) != current:
        raise ValueError(
            "Receiver processed generation changed after transfer registration"
        )


def _canonical_sender_transfer_provenance(
    provenance: TransferProvenance,
) -> TransferProvenance:
    canonical = _transfer_provenance(provenance)
    canonical.pop("media_uploads", None)
    canonical.pop("publication_predecessor", None)
    canonical.pop("case_resolution", None)
    return canonical


def create_or_reuse_transfer_job(
    *,
    transfer_key: str,
    source_node: NetworkNode,
    target_node: NetworkNode,
    source_center: Center | None,
    resource_kind: str,
    resource_hash: str,
    transfer_mode: str,
    processing_policy: str,
    processing_intent: str,
    cleanup_policy: str,
    payload_schema_version: str,
    resource_rows: Mapping[str, JsonValue],
    processing_snapshot: Mapping[str, JsonValue],
    provenance: TransferProvenance,
    created_by: object | None = None,
) -> tuple[TransferJob, bool]:
    if "publication_predecessor" in provenance:
        raise ValueError("Publication predecessor is owned by the receiver")
    reject_transferred_identity_hashes((resource_rows, processing_snapshot, provenance))
    if payload_schema_version != "7.0":
        raise ValueError("Only hub payload schema 7.0 is accepted")
    transfer_job_manager = cast(Any, TransferJob.objects)
    source_node_pk = cast(int, source_node.pk)
    target_node_pk = cast(int, target_node.pk)
    normalized_provenance = _normalized_transfer_provenance(
        provenance=provenance,
        source_node=source_node,
        target_node=target_node,
        source_center=source_center,
        transfer_mode=transfer_mode,
        processing_policy=processing_policy,
        cleanup_policy=cleanup_policy,
    )

    def reuse_existing(existing: TransferJob) -> tuple[TransferJob, bool]:
        """Return an exact replay and reject any canonical identity mismatch."""

        existing_source_node_id = cast(int, getattr(existing, "source_node_id"))
        existing_target_node_id = cast(int, getattr(existing, "target_node_id"))
        existing_source_center_id = cast(
            int | None,
            getattr(existing, "source_center_id"),
        )
        identity_mismatch = (
            existing_source_node_id != source_node_pk
            or existing_target_node_id != target_node_pk
            or existing.resource_kind != resource_kind
            or existing.resource_hash != resource_hash
        )
        payload_matches = (
            existing_source_center_id == getattr(source_center, "pk", None)
            and existing.transfer_mode == transfer_mode
            and existing.processing_policy == processing_policy
            and existing.processing_intent == processing_intent
            and existing.cleanup_policy == cleanup_policy
            and existing.payload_schema_version == payload_schema_version
            and existing.resource_rows == dict(resource_rows)
            and existing.processing_snapshot == dict(processing_snapshot)
            and _canonical_sender_transfer_provenance(
                _transfer_provenance(existing.provenance)
            )
            == _canonical_sender_transfer_provenance(normalized_provenance)
        )
        if identity_mismatch or not payload_matches:
            emit_hub_audit_event(
                "hub.transfer_job_replay_rejected",
                transfer_job_id=str(existing.id),
                source_system="transfer",
                request_user=created_by,
                center_key=(
                    source_center.center_key if source_center is not None else None
                ),
                transfer_key=transfer_key,
                source_node_key=source_node.node_key,
                reason="canonical_payload_mismatch",
            )
            raise ValueError(
                "transfer_key already exists for a different transfer payload"
            )
        emit_hub_audit_event(
            "hub.transfer_job_reused",
            transfer_job_id=str(existing.id),
            source_system="transfer",
            request_user=created_by,
            center_key=source_center.center_key if source_center is not None else None,
            transfer_key=transfer_key,
            source_node_key=source_node.node_key,
        )
        return existing, False

    existing = transfer_job_manager.filter(transfer_key=transfer_key).first()
    if existing is not None:
        return reuse_existing(cast(TransferJob, existing))

    try:
        # A savepoint keeps the caller's outer registration transaction usable
        # when another request wins the unique transfer_key insert race.
        with transaction.atomic():
            transfer_job = transfer_job_manager.create(
                transfer_key=transfer_key,
                source_node=source_node,
                target_node=target_node,
                source_center=source_center,
                resource_kind=resource_kind,
                resource_hash=resource_hash,
                transfer_mode=transfer_mode,
                processing_policy=processing_policy,
                processing_intent=processing_intent,
                cleanup_policy=cleanup_policy,
                payload_schema_version=payload_schema_version,
                resource_rows=resource_rows,
                processing_snapshot=processing_snapshot,
                provenance=normalized_provenance,
                cleanup_status=(
                    TransferJob.CleanupStatus.NOT_REQUESTED
                    if cleanup_policy == TransferJob.CleanupPolicy.RETAIN_ALL.value
                    else TransferJob.CleanupStatus.DEFERRED
                ),
                created_by=(
                    created_by
                    if getattr(created_by, "is_authenticated", False)
                    else None
                ),
            )
    except IntegrityError as error:
        if not _is_transfer_key_unique_violation(error):
            raise
        concurrent = transfer_job_manager.filter(transfer_key=transfer_key).first()
        if concurrent is None:
            raise
        return reuse_existing(cast(TransferJob, concurrent))
    emit_hub_audit_event(
        "hub.transfer_job_created",
        transfer_job_id=str(transfer_job.id),
        source_system="transfer",
        request_user=created_by,
        center_key=source_center.center_key if source_center is not None else None,
        transfer_key=transfer_key,
        source_node_key=source_node.node_key,
        cleanup_policy=cleanup_policy,
    )
    return transfer_job, True


def _resource_ownership_conflict_detail(
    *,
    transfer_job: TransferJob,
    target_object_id: int,
    resource_center_id: int | None,
) -> str | None:
    source_center_id = cast(int | None, getattr(transfer_job, "source_center_id", None))
    if resource_center_id != source_center_id:
        return "Resource hash is already owned by a different center."

    conflicting_source_exists = (
        TransferJob.objects.filter(
            resource_kind=transfer_job.resource_kind,
            resource_hash=transfer_job.resource_hash,
            target_object_id=target_object_id,
        )
        .exclude(pk=transfer_job.pk)
        .exclude(source_node_id=cast(int, getattr(transfer_job, "source_node_id")))
        .exists()
    )
    if conflicting_source_exists:
        return "Resource hash is already owned by a different source node."
    return None


def _mark_transfer_ownership_inconsistent(
    *,
    transfer_job: TransferJob,
    status_detail: str,
) -> TransferJob:
    with transaction.atomic():
        set_transfer_status(transfer_job, TransferJob.TransferStatus.INCONSISTENT)
        transfer_job.processing_decision = (
            TransferJob.ProcessingDecision.MARK_INCONSISTENT
        )
        transfer_job.status_detail = status_detail
        transfer_job.save(
            update_fields=[
                "transfer_status",
                "processing_decision",
                "status_detail",
                "updated_at",
            ]
        )
    emit_hub_audit_event(
        "hub.transfer_resource_ownership_conflict",
        transfer_job_id=str(transfer_job.id),
        source_system="transfer",
        center_key=(
            transfer_job.source_center.center_key
            if transfer_job.source_center is not None
            else None
        ),
        transfer_key=transfer_job.transfer_key,
        source_node_key=transfer_job.source_node.node_key,
        resource_kind=transfer_job.resource_kind,
        resource_hash_sha256=hash_identifier(transfer_job.resource_hash),
        reason="resource_ownership_conflict",
    )
    return transfer_job


def authenticate_network_node(
    *,
    source_node_key: str,
    provided_node_key: str | None = None,
    provided_secret: str | None = None,
) -> NetworkNode | None:
    source_node = NetworkNode.objects.filter(
        node_key=source_node_key, is_active=True
    ).first()
    if source_node is None:
        _log_transfer_node_auth_failure(
            reason="unknown_or_inactive_source_node",
            source_node_key=source_node_key,
            provided_node_key=provided_node_key,
        )
        return None

    normalized_key = str(provided_node_key or "").strip()
    normalized_secret = str(provided_secret or "").strip()

    if normalized_key != source_node.node_key:
        _log_transfer_node_auth_failure(
            reason="node_key_mismatch",
            source_node_key=source_node.node_key,
            provided_node_key=normalized_key,
            source_node_id=cast(int, source_node.pk),
            source_node_role=source_node.role,
        )
        return None

    if not str(source_node.shared_secret_hash or "").strip():
        _log_transfer_node_auth_failure(
            reason="missing_shared_secret_hash",
            source_node_key=source_node.node_key,
            provided_node_key=normalized_key,
            source_node_id=cast(int, source_node.pk),
            source_node_role=source_node.role,
        )
        return None

    if not source_node.check_shared_secret(normalized_secret):
        _log_transfer_node_auth_failure(
            reason="shared_secret_mismatch",
            source_node_key=source_node.node_key,
            provided_node_key=normalized_key,
            source_node_id=cast(int, source_node.pk),
            source_node_role=source_node.role,
        )
        return None
    return source_node


def _log_transfer_node_auth_failure(
    *,
    reason: str,
    source_node_key: str,
    provided_node_key: str | None,
    source_node_id: int | None = None,
    source_node_role: str | None = None,
) -> None:
    normalized_provided_node_key = str(provided_node_key or "").strip()
    emit_hub_audit_event(
        "hub.transfer_node_auth_failed",
        reason=reason,
        source_node_key=source_node_key,
        provided_node_key_present=bool(normalized_provided_node_key),
        provided_node_key_sha256=(
            hash_identifier(normalized_provided_node_key)
            if normalized_provided_node_key
            else None
        ),
        source_node_id=source_node_id,
        source_node_role=source_node_role,
    )


def apply_transfer_metadata(transfer_job: TransferJob) -> TransferJob:
    if transfer_job.resource_kind == TransferJob.ResourceKind.STUDY.value:
        # Archive an authenticated source snapshot. Local ownership and dataset
        # membership are resolved only by explicit researcher acceptance.
        transfer_job.clean()
        if transfer_job.source_center_id != transfer_job.source_node.owning_center_id:
            raise ValueError(
                "Study transfer is outside the authenticated source center"
            )
        with transaction.atomic():
            set_transfer_status(transfer_job, TransferJob.TransferStatus.APPLIED)
            transfer_job.processing_decision = (
                TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE
            )
            transfer_job.status_detail = (
                "Study bundle archived; local researcher acceptance required"
            )
            transfer_job.save(
                update_fields=[
                    "transfer_status",
                    "processing_decision",
                    "status_detail",
                    "updated_at",
                ]
            )
        return transfer_job
    if transfer_job.resource_kind == TransferJob.ResourceKind.VIDEO.value:
        return _apply_video_transfer_metadata(transfer_job)
    if transfer_job.resource_kind == TransferJob.ResourceKind.REPORT.value:
        return _apply_report_transfer_metadata(transfer_job)

    with transaction.atomic():
        set_transfer_status(transfer_job, TransferJob.TransferStatus.FAILED)
        transfer_job.processing_decision = (
            TransferJob.ProcessingDecision.REJECT_TRANSFER
        )
        transfer_job.status_detail = (
            f"resource_kind={transfer_job.resource_kind} is not implemented"
        )
        transfer_job.save(
            update_fields=[
                "transfer_status",
                "processing_decision",
                "status_detail",
                "updated_at",
            ]
        )
    return transfer_job


def attach_transfer_media(
    *,
    transfer_job: TransferJob,
    uploaded_file: UploadedFile,
    media_role: str,
) -> TransferJob:
    """Reject the retired plaintext transfer path before any staging occurs."""

    raise ValueError(
        "Plaintext Hub media attachment is prohibited; use a typed "
        "recipient-encrypted media envelope."
    )


def attach_enveloped_transfer_media(
    *,
    transfer_job: TransferJob,
    ciphertext_stream: BinaryIO,
    ciphertext_size: int,
    media_role: str,
    envelope_json: str,
) -> TransferJob:
    """Authenticate and atomically publish one processed-media envelope."""

    if (
        transfer_job.transfer_status == TransferJob.TransferStatus.INCONSISTENT.value
        or transfer_job.processing_decision
        == TransferJob.ProcessingDecision.REJECT_TRANSFER.value
    ):
        raise ValueError(
            "Media cannot be attached to an inconsistent or rejected transfer."
        )
    if media_role != "processed":
        raise ValueError(
            "Only anonymized processed media may be attached to a transfer."
        )
    if (
        transfer_job.transfer_mode
        != TransferJob.TransferMode.METADATA_AND_PROCESSED_MEDIA.value
    ):
        raise ValueError(
            "Processed media upload requires metadata_and_processed_media transfer mode."
        )

    target: VideoFile | RawPdfFile
    field_name = "processed_file"
    if transfer_job.resource_kind == TransferJob.ResourceKind.VIDEO.value:
        target = _get_transfer_video(transfer_job)
        expected_hash = _expected_processed_video_hash(
            transfer_job=transfer_job,
            video=target,
        )
        suffix = ".mp4"
    elif transfer_job.resource_kind == TransferJob.ResourceKind.REPORT.value:
        target = _get_transfer_report(transfer_job)
        expected_hash = _expected_processed_report_hash(transfer_job=transfer_job)
        suffix = ".pdf"
    else:
        raise ValueError(f"Unsupported resource_kind: {transfer_job.resource_kind}")
    if not expected_hash:
        raise ValueError("Processed media hash is missing from transfer metadata")

    operation_fence: TransferOperationFence | None = None
    operation_heartbeat: TransferOperationHeartbeat | None = None
    if transfer_job.transfer_status != TransferJob.TransferStatus.APPLIED.value:
        transfer_job, operation_fence = _claim_transfer_operation(transfer_job.pk)
        operation_heartbeat = TransferOperationHeartbeat(operation_fence)
        operation_heartbeat.start()

    field_file = getattr(target, field_name)
    original_name = str(getattr(field_file, "name", "") or "")
    candidate_name = ""
    try:
        if operation_fence is not None and operation_fence.orphaned_candidate_name:
            candidate_name = operation_fence.orphaned_candidate_name
            _delete_unpublished_transfer_candidate(
                target=target,
                fence=operation_fence,
                candidate_name=operation_fence.orphaned_candidate_name,
            )
            candidate_name = ""
        with prepare_inbound_hub_envelope(
            transfer_job=transfer_job,
            ciphertext_stream=ciphertext_stream,
            ciphertext_size=ciphertext_size,
            envelope_json=envelope_json,
            media_role=media_role,
        ) as prepared:
            if transfer_job.transfer_status == TransferJob.TransferStatus.APPLIED.value:
                existing_receipt = get_media_envelope_receipt(transfer_job)
                exact_replay = existing_receipt is not None
                if existing_receipt is not None:
                    try:
                        validate_hub_media_receipt_matches_envelope(
                            envelope=prepared.metadata,
                            receipt=existing_receipt,
                        )
                    except ValueError:
                        exact_replay = False
                    exact_replay = exact_replay and (
                        existing_receipt.ciphertext_sha256 == prepared.ciphertext_sha256
                        and existing_receipt.ciphertext_size == prepared.ciphertext_size
                    )
                prepared.accept_exact_replay()
                if exact_replay:
                    return transfer_job
                with transaction.atomic():
                    set_transfer_status(
                        transfer_job, TransferJob.TransferStatus.INCONSISTENT
                    )
                    transfer_job.processing_decision = (
                        TransferJob.ProcessingDecision.MARK_INCONSISTENT
                    )
                    transfer_job.status_detail = (
                        "Applied Hub media transfer received a conflicting envelope replay; "
                        "the previously authenticated artifact was preserved"
                    )
                    transfer_job.save(
                        update_fields=[
                            "transfer_status",
                            "processing_decision",
                            "status_detail",
                            "updated_at",
                        ]
                    )
                raise HubMediaEnvelopeReplayConflict(transfer_job.status_detail)

            if operation_fence is None:
                raise RuntimeError("non-replay transfer upload has no operation fence")
            if operation_heartbeat is None:
                raise RuntimeError("transfer operation heartbeat was not started")
            operation_heartbeat.guard()

            def record_candidate(name: str) -> None:
                nonlocal candidate_name
                assert operation_fence is not None
                _record_transfer_candidate(operation_fence, candidate_name=name)
                candidate_name = name

            candidate_name = _store_model_stream(
                instance=target,
                field_name=field_name,
                source=prepared.plaintext_stream,
                stored_name=(
                    f"{expected_hash}.{operation_fence.attempt_id.hex}.candidate{suffix}"
                ),
                before_write=record_candidate,
            )
            prepared.require_verified()
            operation_heartbeat.guard()
            with transaction.atomic():
                target = (
                    VideoFile.objects.select_for_update(of=("self",))
                    .select_related("state")
                    .get(pk=target.pk)
                    if isinstance(target, VideoFile)
                    else RawPdfFile.objects.select_for_update(of=("self",))
                    .select_related("state")
                    .get(pk=target.pk)
                )
                _require_transfer_publication_predecessor(transfer_job, target)
                if isinstance(
                    target, VideoFile
                ) and video_has_active_media_operation_leases(target.pk):
                    raise TransferOperationBusy(
                        "Video publication waits for active media operations"
                    )
                # Publish the generation's metadata under the same transaction as
                # its authenticated bytes; registration must preserve an old master.
                if isinstance(target, VideoFile):
                    _apply_video_transfer_metadata(
                        transfer_job, publishing_authenticated_media=True
                    )
                else:
                    _apply_report_transfer_metadata(
                        transfer_job, publishing_authenticated_media=True
                    )
                target.refresh_from_db()
                original_name = str(target.processed_file.name or "")
                target.processed_file.name = candidate_name
                media_type: Literal["video", "pdf"]
                if isinstance(target, VideoFile):
                    target.processed_video_hash = expected_hash
                    target.save(
                        update_fields=[
                            "processed_file",
                            "processed_video_hash",
                            "date_modified",
                        ]
                    )
                    _mark_video_transfer_as_processed(target, transfer_job)
                    media_type = "video"
                else:
                    target.save(update_fields=["processed_file", "date_modified"])
                    _mark_report_transfer_as_processed(target, transfer_job)
                    media_type = "pdf"

                processing_decision = (
                    TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE.value
                )
                metadata = prepared.metadata
                receipt = HubMediaEnvelopeReceipt(
                    envelope_contract_version=metadata.contract_version,
                    profile=metadata.profile,
                    transfer_key=metadata.transfer_key,
                    source_node_key=metadata.source_node_key,
                    source_center_key=metadata.source_center_key,
                    target_node_key=metadata.target_node_key,
                    resource_kind=metadata.resource_kind,
                    resource_hash=metadata.resource_hash,
                    processed_media_hash=metadata.processed_media_hash,
                    transfer_mode=metadata.transfer_mode,
                    media_role=metadata.media_role,
                    plaintext_sha256=metadata.plaintext_sha256,
                    plaintext_size=metadata.plaintext_size,
                    recipient_key_id=metadata.recipient_key_id,
                    ciphertext_sha256=prepared.ciphertext_sha256,
                    ciphertext_size=prepared.ciphertext_size,
                    envelope_fingerprint_sha256=(prepared.envelope_fingerprint_sha256),
                    receiver_transfer_id=str(transfer_job.pk),
                    processing_decision=processing_decision,
                )
                _record_media_upload(
                    transfer_job=transfer_job,
                    media_role=media_role,
                    stored_name=candidate_name,
                    content_hash=expected_hash,
                    envelope_receipt=receipt,
                )
                _apply_case_resolution_for_media(
                    transfer_job=transfer_job,
                    media_obj=target,
                    media_type=media_type,
                )
                applied_transfer_job = _save_transfer_job_state(
                    transfer_job=transfer_job,
                    target_object_id=target.pk,
                    transfer_status=TransferJob.TransferStatus.APPLIED,
                    processing_decision=processing_decision,
                    status_detail=(
                        "Envelope-authenticated processed media uploaded and sender "
                        "processing state preserved"
                    ),
                    operation_fence=operation_fence,
                )
                if original_name and original_name != candidate_name:
                    _delete_replaced_generation_after_commit(
                        instance=target,
                        field_name=field_name,
                        replaced_name=original_name,
                    )
                return applied_transfer_job
    except Exception as exc:
        if candidate_name and candidate_name != original_name:
            # Leaving the envelope context can fail after the transaction committed.
            # A persisted reference, not this exception, decides candidate ownership.
            published = (
                VideoFile.objects.filter(pk=target.pk, processed_file=candidate_name)
                if isinstance(target, VideoFile)
                else RawPdfFile.objects.filter(
                    pk=target.pk, processed_file=candidate_name
                )
            ).exists()
            if published:
                emit_hub_audit_event(
                    "hub.transfer_post_publication_cleanup_failed",
                    transfer_id=str(transfer_job.pk),
                    exception_type=type(exc).__name__,
                )
                raise
            if operation_fence is None:
                raise RuntimeError(
                    "Candidate cleanup requires transfer ownership"
                ) from exc
            _delete_unpublished_transfer_candidate(
                target=target,
                fence=operation_fence,
                candidate_name=candidate_name,
            )
        if operation_fence is not None:
            try:
                _fail_transfer_operation(
                    transfer_job=transfer_job,
                    operation_fence=operation_fence,
                    target_object_id=target.pk,
                    processing_decision=transfer_job.processing_decision,
                    status_detail=str(exc),
                    retryable=isinstance(exc, TransferOperationBusy),
                )
            except RuntimeError:
                logger.warning(
                    "Transfer failure was rejected by an expired or replaced fence: %s",
                    transfer_job.transfer_key,
                )
        raise
    finally:
        if operation_heartbeat is not None:
            operation_heartbeat.stop()


def _apply_video_transfer_metadata(
    transfer_job: TransferJob, *, publishing_authenticated_media: bool = False
) -> TransferJob:
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    _assert_privacy_preserving_resource_rows(resource_rows)
    video_file_payload = _json_object(
        resource_rows.get("video_file") or {},
        field_name="resource_rows.video_file",
    )
    source_center = transfer_job.source_center

    if source_center is None:
        with transaction.atomic():
            set_transfer_status(transfer_job, TransferJob.TransferStatus.FAILED)
            transfer_job.processing_decision = (
                TransferJob.ProcessingDecision.REJECT_TRANSFER
            )
            transfer_job.status_detail = (
                "No source_center could be resolved for transfer"
            )
            transfer_job.save(
                update_fields=[
                    "transfer_status",
                    "processing_decision",
                    "status_detail",
                    "updated_at",
                ]
            )
        return transfer_job

    with transaction.atomic():
        video = (
            VideoFile.objects.select_for_update(of=("self",))
            .select_related("state", "sensitive_meta")
            .filter(raw_video_hash=transfer_job.resource_hash)
            .first()
        )

        if video is None:
            video = VideoFile(
                raw_video_hash=transfer_job.resource_hash,
                center=source_center,
            )
        else:
            ownership_conflict = _resource_ownership_conflict_detail(
                transfer_job=transfer_job,
                target_object_id=video.pk,
                resource_center_id=video.center_id,
            )
            if ownership_conflict is not None:
                return _mark_transfer_ownership_inconsistent(
                    transfer_job=transfer_job,
                    status_detail=ownership_conflict,
                )

        if not publishing_authenticated_media:
            _update_transfer_provenance(
                transfer_job,
                publication_predecessor=_publication_predecessor(video).model_dump(
                    mode="json"
                ),
            )
        if video.pk and video.processed_file and not publishing_authenticated_media:
            decision, status, detail = _await_transfer_media(transfer_job)
            return _save_transfer_job_state(
                transfer_job=transfer_job,
                target_object_id=video.pk,
                transfer_status=status,
                processing_decision=decision,
                status_detail=detail,
            )

        sensitive_meta_payload = resource_rows.get("sensitive_meta") or {}
        if isinstance(sensitive_meta_payload, dict) and sensitive_meta_payload:
            sensitive_meta = _upsert_sensitive_meta(
                existing=video.sensitive_meta if video.pk else None,
                payload=cast(JsonObject, sensitive_meta_payload),
                center=source_center,
                source_node=transfer_job.source_node,
            )
            video.sensitive_meta = sensitive_meta

        video.center = source_center
        previous_processed_hash = video.processed_video_hash
        _sync_payload_fields(video, video_file_payload, _VIDEO_FILE_SYNC_FIELDS)
        video_segments = _verified_video_segments(
            video=video,
            provenance=_accept_transfer_video_provenance(
                video, transfer_job.provenance
            ),
            rows=_json_object_list(
                resource_rows.get("video_segments"),
                field_name="resource_rows.video_segments",
            ),
        )

        video.save()
        get_or_create_video_state(video)
        # Sender state remains a claim in resource_rows until media authentication.

        _apply_case_resolution_for_media(
            transfer_job=transfer_job,
            media_obj=video,
            media_type="video",
        )
        with suppress_label_video_segment_state_side_effects():
            for video_segment in video_segments:
                _upsert_video_segment(
                    transfer_job=transfer_job,
                    video=video,
                    payload=video_segment,
                )
        _apply_frame_annotation_rows(
            transfer_job=transfer_job,
            video=video,
            rows=_json_object_list(
                resource_rows.get("frame_annotations"),
                field_name="resource_rows.frame_annotations",
            ),
        )
        _apply_patient_examination_report_rows(
            transfer_job=transfer_job,
            rows=_json_object_list(
                resource_rows.get("reports"),
                field_name="resource_rows.reports",
            ),
        )
        if publishing_authenticated_media:
            transfer_job.target_object_id = video.pk
            return transfer_job

        video.processed_video_hash = previous_processed_hash
        video.save(update_fields=["processed_video_hash", "date_modified"])
        processing_decision, transfer_status, status_detail = _await_transfer_media(
            transfer_job
        )

        transfer_job.target_object_id = video.pk
        transfer_job.processing_decision = processing_decision
        with transaction.atomic():
            set_transfer_status(transfer_job, transfer_status)
            transfer_job.status_detail = status_detail
            transfer_job.save(
                update_fields=[
                    "target_object_id",
                    "processing_decision",
                    "transfer_status",
                    "status_detail",
                    "linked_patient_id",
                    "linked_patient_examination_id",
                    "case_resolution_status",
                    "provenance",
                    "updated_at",
                ]
            )

    return transfer_job


def _apply_report_transfer_metadata(
    transfer_job: TransferJob, *, publishing_authenticated_media: bool = False
) -> TransferJob:
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    _assert_privacy_preserving_resource_rows(resource_rows)
    report_payload = _json_object(
        resource_rows.get("raw_pdf_file") or {},
        field_name="resource_rows.raw_pdf_file",
    )
    source_center = transfer_job.source_center

    if source_center is None:
        with transaction.atomic():
            set_transfer_status(transfer_job, TransferJob.TransferStatus.FAILED)
            transfer_job.processing_decision = (
                TransferJob.ProcessingDecision.REJECT_TRANSFER
            )
            transfer_job.status_detail = (
                "No source_center could be resolved for transfer"
            )
            transfer_job.save(
                update_fields=[
                    "transfer_status",
                    "processing_decision",
                    "status_detail",
                    "updated_at",
                ]
            )
        return transfer_job

    with transaction.atomic():
        report = (
            RawPdfFile.objects.select_for_update(of=("self",))
            .select_related("state", "sensitive_meta")
            .filter(pdf_hash=transfer_job.resource_hash)
            .first()
        )
        if report is None:
            report = RawPdfFile(
                pdf_hash=transfer_job.resource_hash,
                center=source_center,
            )
        else:
            ownership_conflict = _resource_ownership_conflict_detail(
                transfer_job=transfer_job,
                target_object_id=report.pk,
                resource_center_id=report.center_id,
            )
            if ownership_conflict is not None:
                return _mark_transfer_ownership_inconsistent(
                    transfer_job=transfer_job,
                    status_detail=ownership_conflict,
                )

        if not publishing_authenticated_media:
            _update_transfer_provenance(
                transfer_job,
                publication_predecessor=_publication_predecessor(report).model_dump(
                    mode="json"
                ),
            )
        if report.pk and report.processed_file and not publishing_authenticated_media:
            decision, status, detail = _await_transfer_media(transfer_job)
            return _save_transfer_job_state(
                transfer_job=transfer_job,
                target_object_id=report.pk,
                transfer_status=status,
                processing_decision=decision,
                status_detail=detail,
            )

        sensitive_meta_payload = resource_rows.get("sensitive_meta") or {}
        if isinstance(sensitive_meta_payload, dict) and sensitive_meta_payload:
            sensitive_meta = _upsert_sensitive_meta(
                existing=report.sensitive_meta if report.pk else None,
                payload=cast(JsonObject, sensitive_meta_payload),
                center=source_center,
                source_node=transfer_job.source_node,
            )
            report.sensitive_meta = sensitive_meta

        report.center = source_center
        _sync_payload_fields(
            report,
            report_payload,
            _REPORT_FILE_SYNC_FIELDS
            if publishing_authenticated_media
            else ("anonymized_text",),
        )

        report.save()
        get_or_create_raw_pdf_state(report)
        # Sender state remains a claim in resource_rows until media authentication.

        _apply_case_resolution_for_media(
            transfer_job=transfer_job,
            media_obj=report,
            media_type="pdf",
        )
        _apply_patient_examination_report_rows(
            transfer_job=transfer_job,
            rows=_json_object_list(
                resource_rows.get("reports"),
                field_name="resource_rows.reports",
            ),
        )

        if publishing_authenticated_media:
            transfer_job.target_object_id = report.pk
            return transfer_job

        processing_decision, transfer_status, status_detail = _await_transfer_media(
            transfer_job
        )

        transfer_job.target_object_id = report.pk
        transfer_job.processing_decision = processing_decision
        with transaction.atomic():
            set_transfer_status(transfer_job, transfer_status)
            transfer_job.status_detail = status_detail
            transfer_job.save(
                update_fields=[
                    "target_object_id",
                    "processing_decision",
                    "transfer_status",
                    "status_detail",
                    "linked_patient_id",
                    "linked_patient_examination_id",
                    "case_resolution_status",
                    "provenance",
                    "updated_at",
                ]
            )

    return transfer_job


def _fenced_transfer(fence: TransferOperationFence) -> QuerySet[TransferJob]:
    """The transfer row while the fenced operation still owns a live lease."""
    return TransferJob.objects.filter(
        pk=fence.transfer_job_id,
        attempt_id=fence.attempt_id,
        operation_owner=fence.owner_id,
        operation_fencing_token=fence.fencing_token,
        operation_lease_expires_at__gt=Now(),
    )


def _save_transfer_job_state(
    *,
    transfer_job: TransferJob,
    target_object_id: int | None,
    transfer_status: str,
    processing_decision: str,
    status_detail: str,
    operation_fence: TransferOperationFence | None = None,
) -> TransferJob:
    transfer_job.target_object_id = target_object_id
    transfer_job.processing_decision = processing_decision
    transfer_job.status_detail = status_detail
    update_fields = [
        "target_object_id",
        "transfer_status",
        "processing_decision",
        "status_detail",
        "linked_patient_id",
        "linked_patient_examination_id",
        "case_resolution_status",
        "provenance",
        "updated_at",
    ]
    if operation_fence is None:
        with transaction.atomic():
            set_transfer_status(transfer_job, transfer_status)
            transfer_job.save(update_fields=update_fields)
        return transfer_job

    if transfer_status != TransferJob.TransferStatus.APPLIED.value:
        raise ValueError("fenced transfer success must publish APPLIED status")
    transfer_job.clean()
    updated = update_transfer_status(
        _fenced_transfer(operation_fence),
        expected=TransferJob.TransferStatus.RUNNING,
        target=transfer_status,
        case_resolution_status=transfer_job.case_resolution_status,
        target_object_id=target_object_id,
        processing_decision=processing_decision,
        status_detail=status_detail,
        linked_patient_id=transfer_job.linked_patient_id,
        linked_patient_examination_id=transfer_job.linked_patient_examination_id,
        provenance=transfer_job.provenance,
        attempt_id=None,
        operation_owner="",
        operation_heartbeat_at=None,
        operation_lease_expires_at=None,
        operation_candidate_name="",
        updated_at=Now(),
    )
    if updated != 1:
        raise RuntimeError("transfer completion rejected by ownership fence")
    transfer_job.refresh_from_db()
    return transfer_job


def _fail_transfer_operation(
    *,
    transfer_job: TransferJob,
    operation_fence: TransferOperationFence,
    target_object_id: int | None,
    processing_decision: str,
    status_detail: str,
    retryable: bool = False,
) -> TransferJob:
    updated = update_transfer_status(
        _fenced_transfer(operation_fence),
        expected=TransferJob.TransferStatus.RUNNING,
        target=(
            TransferJob.TransferStatus.AWAITING_MEDIA
            if retryable
            else TransferJob.TransferStatus.FAILED
        ),
        case_resolution_status=transfer_job.case_resolution_status,
        target_object_id=target_object_id,
        processing_decision=processing_decision,
        status_detail=status_detail,
        linked_patient_id=transfer_job.linked_patient_id,
        linked_patient_examination_id=transfer_job.linked_patient_examination_id,
        provenance=transfer_job.provenance,
        attempt_id=None,
        operation_owner="",
        operation_heartbeat_at=None,
        operation_lease_expires_at=None,
        operation_candidate_name="",
        updated_at=Now(),
    )
    if updated != 1:
        raise RuntimeError("transfer failure rejected by ownership fence")
    transfer_job.refresh_from_db()
    return transfer_job


def _delete_unpublished_transfer_candidate(
    *,
    target: VideoFile | RawPdfFile,
    fence: TransferOperationFence,
    candidate_name: str,
) -> None:
    """Delete only the fenced staging artifact, retaining intent if deletion fails."""
    field_file = cast(FieldFile, target.processed_file)
    storage = field_file.storage
    if not isinstance(storage, (EncryptedStorage, LazyEncryptedStorage)):
        raise ValueError("Candidate cleanup requires encrypted storage")
    with transaction.atomic():
        # Match publication's resource-before-transfer lock order.
        if isinstance(target, VideoFile):
            VideoFile.objects.select_for_update().get(pk=target.pk)
        else:
            RawPdfFile.objects.select_for_update().get(pk=target.pk)
        owned = (
            _fenced_transfer(fence)
            .select_for_update()
            .filter(
                operation_candidate_name=candidate_name,
            )
            .first()
        )
        if owned is None:
            raise RuntimeError("Candidate cleanup rejected by ownership fence")
        if stored_media_name_referenced(candidate_name):
            raise RuntimeError("Candidate cleanup cannot delete published media")
        # This is staging, not the canonical FieldFile: encrypted storage owns
        # its path lock and audited safe_unlink_file operation.
        storage.delete(candidate_name)
        _fenced_transfer(fence).update(operation_candidate_name="", updated_at=Now())


def _store_model_stream(
    *,
    instance: VideoFile | RawPdfFile,
    field_name: str,
    source: BinaryIO,
    stored_name: str,
    before_write: Callable[[str], None],
) -> str:
    """Publish a stream as a new storage generation without deleting the old one."""

    field_file = cast(FieldFile, getattr(instance, field_name))
    storage_name = field_file.field.generate_filename(instance, stored_name)
    django_file = File(source, name=stored_name)
    storage = field_file.storage
    if not isinstance(storage, (EncryptedStorage, LazyEncryptedStorage)):
        raise ValueError(
            "Hub media publication requires intent-aware encrypted storage"
        )
    saved_name = storage.save_with_intent(
        storage_name, django_file, before_write=before_write
    )
    field_file.name = saved_name
    return saved_name


def _delete_replaced_generation_after_commit(
    *,
    instance: Any,
    field_name: str,
    replaced_name: str,
) -> None:
    """Delete exactly one superseded storage generation after database commit."""

    current_field = cast(FieldFile, getattr(instance, field_name))
    replaced_field = FieldFile(instance, current_field.field, replaced_name)
    transaction.on_commit(
        partial(safe_delete_field_file, replaced_field, missing_ok=True),
        robust=True,
    )


def _record_media_upload(
    *,
    transfer_job: TransferJob,
    media_role: str,
    stored_name: str,
    content_hash: str,
    envelope_receipt: HubMediaEnvelopeReceipt | None = None,
) -> None:
    provenance = _transfer_provenance(transfer_job.provenance)
    uploads = list(provenance.get("media_uploads") or [])
    upload: dict[str, JsonValue] = {
        "media_role": media_role,
        "stored_name": stored_name,
        "content_hash": content_hash,
        "uploaded_name": Path(stored_name).name,
    }
    if envelope_receipt is not None:
        upload["envelope_receipt"] = cast(
            JsonValue,
            envelope_receipt.model_dump(mode="json"),
        )
    uploads.append(upload)
    _update_transfer_provenance(transfer_job, media_uploads=uploads)


def get_media_envelope_receipt(
    transfer_job: TransferJob,
) -> HubMediaEnvelopeReceipt | None:
    provenance = _transfer_provenance(transfer_job.provenance)
    uploads = provenance.get("media_uploads") or []
    for upload in reversed(uploads):
        receipt_value = upload.get("envelope_receipt")
        if receipt_value is not None:
            try:
                return HubMediaEnvelopeReceipt.model_validate(receipt_value)
            except ValueError as exc:
                raise ValueError(
                    "Persisted Hub media envelope receipt is invalid"
                ) from exc
    return None


def _get_transfer_video(transfer_job: TransferJob) -> VideoFile:
    queryset = VideoFile.objects.select_related("center", "state", "sensitive_meta")
    video = None
    if transfer_job.target_object_id is not None:
        video = queryset.filter(pk=transfer_job.target_object_id).first()
    if video is None:
        video = queryset.filter(raw_video_hash=transfer_job.resource_hash).first()
    if video is None:
        raise ValueError("Transfer target video could not be resolved")
    return video


def _get_transfer_report(transfer_job: TransferJob) -> RawPdfFile:
    queryset = RawPdfFile.objects.select_related("center", "state", "sensitive_meta")
    report = None
    if transfer_job.target_object_id is not None:
        report = queryset.filter(pk=transfer_job.target_object_id).first()
    if report is None:
        report = queryset.filter(pdf_hash=transfer_job.resource_hash).first()
    if report is None:
        raise ValueError("Transfer target report could not be resolved")
    return report


def _expected_processed_video_hash(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
) -> str:
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    video_payload = resource_rows.get("video_file") or {}
    if not isinstance(video_payload, dict):
        return str(video.processed_video_hash or "").strip()
    payload_hash = str(video_payload.get("processed_video_hash", "")).strip()
    if payload_hash:
        return payload_hash
    return str(video.processed_video_hash or "").strip()


def _expected_processed_report_hash(*, transfer_job: TransferJob) -> str:
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    state_payload = resource_rows.get("raw_pdf_state") or {}
    if not isinstance(state_payload, dict):
        return ""
    return str(state_payload.get("processed_file_sha256", "") or "").strip()


def _mark_video_transfer_as_processed(
    video: VideoFile, transfer_job: TransferJob
) -> None:
    from endoreg_db.services.hub.video_provenance import (
        validate_received_video_timeline,
    )

    validate_received_video_timeline(video)
    state = get_or_create_video_state(video)
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    payload = _json_object(
        resource_rows.get("video_state") or {}, field_name="video_state"
    )
    _save_synced_payload_fields(state, payload, _VIDEO_STATE_SYNC_FIELDS)
    _reconcile_segment_annotations_validated(
        video_state=state, sender_state_payload=payload
    )
    update_media_state(
        state,
        changes={
            "processing_started": True,
            "anonymized": True,
            "sensitive_meta_processed": True,
            "anonymization_validated": True,
            "processed_file_sha256": str(video.processed_video_hash or "").strip(),
        },
    )
    ProcessingHistory.mark_success(file_hash=video.raw_video_hash, obj=video)


def _mark_report_transfer_as_processed(
    report: RawPdfFile, transfer_job: TransferJob
) -> None:
    state = get_or_create_raw_pdf_state(report)
    actual_hash = get_file_hash(report.processed_file)
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    payload = _json_object(
        resource_rows.get("raw_pdf_state") or {}, field_name="raw_pdf_state"
    )
    _save_synced_payload_fields(state, payload, _REPORT_STATE_SYNC_FIELDS)
    update_media_state(
        state,
        changes={
            "processing_started": True,
            "anonymized": True,
            "sensitive_meta_processed": True,
            "anonymization_validated": True,
            "processed_file_sha256": actual_hash,
        },
    )
    ProcessingHistory.mark_success(file_hash=report.pdf_hash, obj=report)


def _apply_frame_annotation_rows(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
    rows: list[JsonObject],
) -> None:
    if not rows:
        return

    timeline = _processed_timeline(resolve_video_provenance(video))
    for row in rows:
        row_video_hash = str(row.get("raw_video_hash") or "").strip()
        if row_video_hash and row_video_hash != video.raw_video_hash:
            raise ValueError(
                "resource_rows.frame_annotations raw_video_hash does not match transfer video"
            )

        frame_number = _json_int(
            row["frame_number"],
            field_name="resource_rows.frame_annotations.frame_number",
        )
        relative_path = str(row["frame_relative_path"]).strip()
        label_name = str(row["label_name"]).strip()
        information_source_name = str(row["information_source_name"]).strip()
        frame_timestamp = _json_float(
            row.get("frame_timestamp"),
            field_name="resource_rows.frame_annotations.frame_timestamp",
        )
        if not 0 <= frame_number < len(timeline.presentation_timestamps):
            raise ValueError(
                "Transferred annotation frame exceeds the processed presentation timeline"
            )
        presentation_timestamp = timeline.presentation_timestamps[frame_number]
        canonical_timestamp = presentation_timestamp_seconds(
            presentation_timestamp, timeline.time_base_num, timeline.time_base_den
        )
        if frame_timestamp is not None:
            validate_presentation_timestamp_seconds(
                presentation_timestamp,
                timeline.time_base_num,
                timeline.time_base_den,
                frame_timestamp,
            )

        frame, _ = Frame.objects.get_or_create(
            video=video,
            frame_number=frame_number,
            defaults={
                "relative_path": relative_path,
                "timestamp": canonical_timestamp,
                "presentation_timestamp": presentation_timestamp,
            },
        )
        if frame.timestamp is not None:
            validate_presentation_timestamp_seconds(
                presentation_timestamp,
                timeline.time_base_num,
                timeline.time_base_den,
                frame.timestamp,
            )
        if (
            frame.presentation_timestamp is not None
            and frame.presentation_timestamp != presentation_timestamp
        ):
            raise ValueError(
                "Existing annotation frame coordinates disagree with the processed presentation timeline"
            )
        frame_update_fields: list[str] = []
        if frame.relative_path != relative_path:
            frame.relative_path = relative_path
            frame_update_fields.append("relative_path")
        if frame.timestamp is None:
            frame.timestamp = canonical_timestamp
            frame_update_fields.append("timestamp")
        if frame.presentation_timestamp is None:
            frame.presentation_timestamp = presentation_timestamp
            frame_update_fields.append("presentation_timestamp")
        if frame_update_fields:
            frame.save(update_fields=frame_update_fields)

        label, _ = Label.get_or_create_from_name(label_name)
        information_source, _ = cast(
            InformationSourceManager, InformationSource.objects
        ).get_or_create_by_name(
            information_source_name,
            description="Imported from hub transfer frame annotations",
        )
        external_annotation_id = _transfer_annotation_external_id(
            transfer_job=transfer_job,
            row=row,
        )
        _upsert_frame_annotation(
            frame=frame,
            label=label,
            information_source=information_source,
            annotator=None,
            value=_json_bool(
                row["value"],
                field_name="resource_rows.frame_annotations.value",
            ),
            float_value=_json_float(
                row.get("float_value"),
                field_name="resource_rows.frame_annotations.float_value",
            ),
            external_annotation_id=external_annotation_id,
        )

    video_state = get_or_create_video_state(video)
    if not video_state.frame_annotations_generated:
        update_media_state(video_state, changes={"frame_annotations_generated": True})


def _processed_timeline(
    provenance: VideoProvenanceBundle | VideoProvenanceRecovery,
) -> VideoFrameTimeline:
    """Presentation timeline of the published processed generation."""
    if isinstance(provenance, VideoProvenanceRecovery):
        return provenance.processed_timeline
    return provenance.transformations[-1].output


def _accept_transfer_video_provenance(
    video: VideoFile, transfer_provenance: Mapping[str, object]
) -> VideoProvenanceBundle | VideoProvenanceRecovery | None:
    """Persist the sender's provenance and bind frame_count to its timestamps.

    Received presentation timestamps are the single source of truth: the
    declared ``frame_count`` must equal the processed timeline length.
    """
    from endoreg_db.services.hub.video_provenance import (
        accept_video_provenance,
        accept_video_provenance_recovery,
    )

    frame_provenance = transfer_provenance.get("video_frame_provenance")
    recovery = transfer_provenance.get("video_provenance_recovery")
    provenance: VideoProvenanceBundle | VideoProvenanceRecovery
    if frame_provenance is not None and recovery is not None:
        raise ValueError("Transfer must select verified or unresolved provenance")
    if frame_provenance is not None:
        provenance = VideoProvenanceBundle.model_validate(frame_provenance)
        accept_video_provenance(video, provenance)
    elif recovery is not None:
        provenance = VideoProvenanceRecovery.model_validate(recovery)
        accept_video_provenance_recovery(video, provenance)
    else:
        return None

    timeline_frame_count = len(_processed_timeline(provenance).presentation_timestamps)
    if video.frame_count not in (None, timeline_frame_count):
        raise ValueError(
            "resource_rows.video_file.frame_count disagrees with the received "
            "presentation timeline"
        )
    video.frame_count = timeline_frame_count
    return provenance


def _verified_video_segments(
    *,
    video: VideoFile,
    provenance: VideoProvenanceBundle | VideoProvenanceRecovery | None,
    rows: list[JsonObject],
) -> list[HubTransferVideoSegmentPayload]:
    """Recompute sender coordinates from the received provenance; never trust them."""
    if not rows:
        return []
    if provenance is None:
        raise ValueError(
            "Video segments require frame provenance or an unresolved receipt"
        )
    segments = [HubTransferVideoSegmentPayload.model_validate(row) for row in rows]
    expected = segment_timeline_coordinates(
        provenance,
        processed_content_hash=video.processed_video_hash,
        segments=[
            SegmentFrameRange(
                segment_id=position,
                start_frame_number=segment.start_frame_number,
                end_frame_number_exclusive=segment.end_frame_number_exclusive,
            )
            for position, segment in enumerate(segments)
        ],
    )
    for segment, coordinates in zip(segments, expected, strict=True):
        if wire_timeline_coordinates(coordinates) != segment.timeline_coordinates:
            raise ValueError(
                f"video segment {segment.source_segment_id} timeline_coordinates "
                "disagree with the received provenance"
            )
    return segments


def _upsert_video_segment(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
    payload: HubTransferVideoSegmentPayload,
) -> None:
    """Idempotently upsert one PTS-verified segment by its source-scoped identity."""
    if payload.source_node_key != transfer_job.source_node.node_key:
        raise ValueError(
            "resource_rows.video_segments source_node_key does not match transfer"
        )
    if payload.raw_video_hash != video.raw_video_hash:
        raise ValueError(
            "resource_rows.video_segments raw_video_hash does not match transfer video"
        )

    source_segment_id = str(payload.source_segment_id)
    label, _ = Label.get_or_create_from_name(payload.label_name.strip())
    information_source, _ = cast(
        InformationSourceManager, InformationSource.objects
    ).get_or_create_by_name(
        payload.anonymous_provenance.information_source_name.strip(),
        description="Anonymous provenance imported from a hub transfer segment",
    )

    segment = (
        LabelVideoSegment.objects.select_related("video_file")
        .filter(
            source_node_key=payload.source_node_key,
            source_segment_id=source_segment_id,
        )
        .first()
    )
    if segment is not None and segment.video_file.pk != video.pk:
        raise ValueError(
            "source-scoped segment identity is already linked to another video"
        )
    if segment is None:
        segment = LabelVideoSegment(
            source_node_key=payload.source_node_key,
            source_segment_id=source_segment_id,
            video_file=video,
        )

    segment.video_file = video
    segment.start_frame_number = payload.start_frame_number
    segment.end_frame_number = payload.end_frame_number_exclusive
    segment.label = label
    segment.source = information_source
    segment.prediction_meta = _resolve_transferred_segment_prediction_meta(
        video=video, label=label, payload=payload
    )
    segment.export_segment = payload.export_segment
    segment.save()

    existing_state = LabelVideoSegmentState.objects.filter(origin=segment).first()
    state = existing_state or LabelVideoSegmentState(origin=segment)
    state.prediction = payload.source_kind == "prediction"
    state.annotation = payload.source_kind == "manual_annotation"
    state.is_validated = payload.validation_state == "validated"
    if existing_state is None:
        models.Model.save(state)
    else:
        models.Model.save(
            state, update_fields=["prediction", "annotation", "is_validated"]
        )


def _resolve_transferred_segment_prediction_meta(
    *,
    video: VideoFile,
    label: Label,
    payload: HubTransferVideoSegmentPayload,
) -> VideoPredictionMeta | None:
    # The payload contract pairs model_name/model_version and limits them to predictions.
    if payload.model_name is None or payload.model_version is None:
        return None
    candidates = list(
        ModelMeta.objects.filter(
            name=payload.model_name,
            version=payload.model_version,
            labelset__labels=label,
        )
        .distinct()
        .order_by("pk")[:2]
    )
    if len(candidates) != 1:
        raise ValueError(
            "prediction segment model_name/model_version must resolve uniquely "
            "and include the transferred label"
        )
    prediction_meta, _ = VideoPredictionMeta.objects.get_or_create(
        video_file=video,
        model_meta=candidates[0],
    )
    return prediction_meta


def _reconcile_segment_annotations_validated(
    *,
    video_state: VideoState,
    sender_state_payload: JsonObject,
) -> None:
    if "segment_annotations_validated" not in sender_state_payload:
        return
    update_media_state(
        video_state,
        changes={
            "segment_annotations_validated": _json_bool(
                sender_state_payload["segment_annotations_validated"],
                field_name="resource_rows.video_state.segment_annotations_validated",
            ),
        },
    )


def _transfer_annotation_external_id(
    *,
    transfer_job: TransferJob,
    row: JsonObject,
) -> str | None:
    source_annotation_id = row.get("annotation_id")
    if source_annotation_id in (None, ""):
        return None
    return (
        f"hub_transfer:{transfer_job.source_node.node_key}:"
        f"annotation:{source_annotation_id}"
    )


def _upsert_frame_annotation(
    *,
    frame: Frame,
    label: Label,
    information_source: InformationSource,
    annotator: str | None,
    value: bool,
    float_value: float | None,
    external_annotation_id: str | None,
) -> None:
    annotation = None
    if external_annotation_id:
        annotation = (
            ImageClassificationAnnotation.objects.filter(
                external_annotation_id=external_annotation_id
            )
            .order_by("pk")
            .first()
        )

    if annotation is None:
        defaults: dict[str, Any] = {
            "value": value,
            "float_value": float_value,
        }
        if external_annotation_id is not None:
            defaults["external_annotation_id"] = external_annotation_id
        ImageClassificationAnnotation.objects.update_or_create(
            frame=frame,
            label=label,
            information_source=information_source,
            annotator=annotator,
            defaults=defaults,
        )
        return

    annotation.frame = frame
    annotation.label = label
    annotation.information_source = information_source
    annotation.annotator = annotator
    annotation.value = value
    annotation.float_value = float_value
    annotation.external_annotation_id = external_annotation_id
    annotation.save(
        update_fields=[
            "frame",
            "label",
            "information_source",
            "annotator",
            "value",
            "float_value",
            "external_annotation_id",
            "date_modified",
        ]
    )


def _apply_patient_examination_report_rows(
    *,
    transfer_job: TransferJob,
    rows: list[JsonObject],
) -> None:
    if not rows:
        return
    if transfer_job.linked_patient_examination_id is None:
        raise ValueError("report rows require a linked patient examination")

    patient_examination = PatientExamination.objects.filter(
        pk=transfer_job.linked_patient_examination_id
    ).first()
    if patient_examination is None:
        raise ValueError("linked patient examination could not be resolved")

    for row in rows:
        _upsert_patient_examination_report(
            patient_examination=patient_examination,
            row=row,
        )


@transaction.atomic
def _upsert_patient_examination_report(
    *,
    patient_examination: PatientExamination,
    row: JsonObject,
) -> None:
    template_name = str(row["template_name"]).strip()
    template_version = str(row.get("template_version") or "")
    template_hash = str(row.get("template_hash") or "")
    version = _json_int(
        row.get("version"),
        field_name="resource_rows.reports.version",
        default=1,
    )

    PatientExamination.objects.select_for_update().get(pk=patient_examination.pk)
    # A transfer row carries template/version metadata only. It cannot identify
    # a local author's examination report, even if those fields happen to match.
    report = (
        PatientExaminationReport.objects.filter(
            patient_examination=patient_examination,
            template_name=template_name,
            template_version=template_version,
            template_hash=template_hash,
            version=version,
            status=PatientExaminationReport.Status.FINAL,
            created_by__isnull=True,
            updated_by__isnull=True,
            finalized_by__isnull=True,
            dtypes_record__isnull=True,
            source_documents__isnull=True,
            rendered_text="",
            editor_payload={},
        )
        .order_by("-id")
        .first()
    )
    if report is not None and report.is_active:
        return
    if report is None:
        report = PatientExaminationReport(
            patient_examination=patient_examination,
            template_name=template_name,
            template_version=template_version,
            template_hash=template_hash,
            version=version,
        )

    set_patient_examination_report_status(
        report, PatientExaminationReport.Status.FINAL.value
    )
    report.is_active = True
    report.save()


# Sender-declared columns mirrored verbatim; frame_count is then bound to the
# received presentation timeline by _accept_transfer_video_provenance.
_VIDEO_FILE_SYNC_FIELDS = (
    "processed_video_hash",
    "fps",
    "duration",
    "frame_count",
    "width",
    "height",
    "suffix",
)
_VIDEO_STATE_SYNC_FIELDS = (
    "frames_extracted",
    "frames_initialized",
    "frame_count",
    "video_meta_extracted",
    "text_meta_extracted",
    "initial_prediction_completed",
    "lvs_created",
    "frame_annotations_generated",
    "sensitive_meta_processed",
    "anonymized",
    "anonymization_validated",
    "outside_segments_removed",
    "processing_error",
    "processing_started",
    "segment_annotations_created",
    "was_created",
    "processed_file_sha256",
)
_REPORT_FILE_SYNC_FIELDS = (
    "anonymized_text",
    "state_report_processing_required",
    "state_report_processed",
)
_REPORT_STATE_SYNC_FIELDS = (
    "text_meta_extracted",
    "initial_prediction_completed",
    "sensitive_meta_processed",
    "anonymized",
    "anonymization_validated",
    "processing_started",
    "processing_error",
    "was_created",
    "pdf_meta_extracted",
    "processed_file_sha256",
)


def _sync_payload_fields(
    instance: models.Model, payload: JsonObject, field_names: tuple[str, ...]
) -> list[str]:
    """Copy present payload fields onto ``instance``; return the names copied."""
    copied = [name for name in field_names if name in payload]
    for name in copied:
        setattr(instance, name, payload[name])
    return copied


def _save_synced_payload_fields(
    instance: VideoState | RawPdfState,
    payload: JsonObject,
    field_names: tuple[str, ...],
) -> None:
    update_media_state(
        instance,
        changes={name: payload[name] for name in field_names if name in payload},
    )


@transaction.atomic
def _upsert_sensitive_meta(
    *,
    existing: SensitiveMeta | None,
    payload: JsonObject,
    center: Center,
    source_node: NetworkNode | None = None,
) -> SensitiveMeta:
    _assert_privacy_preserving_resource_rows(
        cast(JsonObject, {"sensitive_meta": payload})
    )
    identity = HubTransferSensitiveMetaPayload.model_validate(payload)
    if (
        source_node is None
        or getattr(source_node, "owning_center_id", None) != center.pk
    ):
        raise ValueError(
            "Hub identity requires the authenticated source node and its center"
        )
    # Local source UUIDs never arrive here; see hub_record_identity for the namespace.
    hashes = derive_hub_record_identity_hashes(
        source_node_pk=source_node.pk,
        center_pk=center.pk,
        patient_uuid=identity.patient_uuid,
        examination_uuid=identity.examination_uuid,
    )
    payload_patient_hash = hashes.patient_hash
    payload_examination_hash = hashes.examination_hash

    # Serialize hub identity creation within the authenticated center.
    Center.objects.select_for_update().get(pk=center.pk)
    patient = Patient.objects.filter(uuid=identity.patient_uuid).first()
    assert_patient_owned_by_source(
        patient=patient, center_pk=center.pk, patient_hash=payload_patient_hash
    )
    if existing is not None:
        existing = SensitiveMeta.objects.select_for_update().get(pk=existing.pk)
        assert_existing_meta_matches_identity(
            existing=existing,
            center_pk=center.pk,
            patient=patient,
            hashes=hashes,
            resolve_previous_patient=partial(
                Patient.get_pseudo_patient_by_hash, center=center
            ),
        )

    patient_examination = (
        PatientExamination.objects.select_related("patient")
        .filter(uuid=identity.examination_uuid)
        .first()
    )
    assert_examination_matches_identity(
        patient_examination=patient_examination,
        existing=existing,
        patient=patient,
        examination_hash=payload_examination_hash,
    )
    if patient is None:
        patient = Patient.objects.create(
            uuid=identity.patient_uuid,
            first_name="",
            last_name="",
            dob=None,
            gender=None,
            center=center,
            patient_hash=payload_patient_hash,
            is_real_person=False,
        )
    if patient_examination is None:
        patient_examination = PatientExamination.objects.create(
            uuid=identity.examination_uuid,
            patient=patient,
            examination=None,
            hash=payload_examination_hash,
        )
    direct_identifier_updates: dict[str, Any] = {
        "patient_first_name": None,
        "patient_last_name": None,
        "patient_dob": None,
        "examination_date": None,
        "examination_time": None,
        "casenumber": None,
        "file_path": None,
        "examiner_first_name": None,
        "examiner_last_name": None,
        "text": None,
        "anonymized_text": None,
        "endoscope_sn": None,
        "external_id": None,
        "validation_comment": "",
    }
    if existing is None:
        sensitive_meta = SensitiveMeta(
            center=center,
            patient_hash=payload_patient_hash,
            examination_hash=payload_examination_hash,
            **direct_identifier_updates,
        )
        SensitiveMeta.objects.bulk_create([sensitive_meta])
    else:
        sensitive_meta = existing
        SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
            center=center,
            patient_hash=payload_patient_hash,
            examination_hash=payload_examination_hash,
            **direct_identifier_updates,
        )
        sensitive_meta.refresh_from_db()

    SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
        pseudo_patient=patient,
        pseudo_examination=patient_examination,
    )
    sensitive_meta.pseudo_patient = patient
    sensitive_meta.pseudo_examination = patient_examination
    return sensitive_meta


@transaction.atomic
def _apply_case_resolution_for_media(
    *,
    transfer_job: TransferJob,
    media_obj: RawPdfFile | VideoFile,
    media_type: Literal["video", "pdf"],
) -> None:
    if getattr(media_obj, "sensitive_meta", None) is None:
        set_case_resolution_status(
            transfer_job, TransferJob.CaseResolutionStatus.UNRESOLVED
        )
        transfer_job.linked_patient_id = None
        transfer_job.linked_patient_examination_id = None
        return

    sensitive_meta = media_obj.sensitive_meta
    if not sensitive_meta:
        set_case_resolution_status(
            transfer_job, TransferJob.CaseResolutionStatus.UNRESOLVED
        )
        transfer_job.linked_patient_id = None
        transfer_job.linked_patient_examination_id = None
        return

    if not getattr(sensitive_meta, "patient_hash", None) or not getattr(
        sensitive_meta, "examination_hash", None
    ):
        set_case_resolution_status(
            transfer_job, TransferJob.CaseResolutionStatus.UNRESOLVED
        )
        transfer_job.linked_patient_id = None
        transfer_job.linked_patient_examination_id = None
        return

    resolution = auto_resolve_media_case(media_type=media_type, media_obj=media_obj)
    set_case_resolution_status(transfer_job, resolution.status)
    transfer_job.linked_patient_examination_id = (
        cast(int, resolution.patient_examination.pk)
        if resolution.patient_examination is not None
        else None
    )
    transfer_job.linked_patient_id = (
        cast(int | None, getattr(resolution.patient_examination, "patient_id", None))
        if resolution.patient_examination is not None
        else None
    )
    _update_transfer_provenance(
        transfer_job,
        case_resolution={
            "status": resolution.status,
            "created": resolution.created,
            "reason": resolution.reason,
            "linked_patient_examination_id": transfer_job.linked_patient_examination_id,
            "linked_patient_id": transfer_job.linked_patient_id,
        },
    )


def _await_transfer_media(transfer_job: TransferJob) -> tuple[str, str, str]:
    # Registration cannot attest receipt of this envelope, even if another
    # transfer or local import has already produced an artifact for this resource.
    # Exact registration replay returns its existing job before metadata application.
    return (
        TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
        TransferJob.TransferStatus.AWAITING_MEDIA,
        f"Metadata synchronized; awaiting authenticated {transfer_job.resource_kind} media",
    )


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return bool(value)
