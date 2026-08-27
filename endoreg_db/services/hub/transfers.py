# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Literal, NotRequired, TypedDict, cast

from django.core.files import File
from django.db.models.fields.files import FieldFile
from django.core.files.uploadedfile import UploadedFile
from django.db import DatabaseError, IntegrityError, connection, models, transaction
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue
from lx_dtypes.models.contracts.hub_media_envelope import (
    HubMediaEnvelopeReceipt,
    validate_hub_media_receipt_matches_envelope,
)

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
from endoreg_db.services.auto_case_resolution import auto_resolve_media_case
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.file_operations import (
    atomic_handoff_file,
    ensure_directory,
    safe_delete_field_file,
    sha256_file,
)
from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.utils.paths import TRANSCODING_DIR
from endoreg_db.utils.storage import delete_field_file, file_exists, save_local_file
from endoreg_db.utils.structured_logging import hash_identifier
from .ingest import _default_processor_name
from .transfer_envelope import (
    HubMediaEnvelopeReplayConflict,
    prepare_inbound_hub_envelope,
)

logger = logging.getLogger(__name__)

_SAFE_SENSITIVE_META_FIELDS = frozenset({"patient_hash", "examination_hash"})
_RECEIVER_MANAGED_TRANSFER_PROVENANCE_FIELDS = frozenset(
    {"media_uploads", "case_resolution"}
)
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

    if connection.vendor == "sqlite":
        sqlite_detail = f"UNIQUE constraint failed: {table_name}.{column_name}"
        return str(cause or error).strip() == sqlite_detail

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
    sensitive_meta = resource_rows.get("sensitive_meta")
    if isinstance(sensitive_meta, dict):
        forbidden = sorted(set(sensitive_meta).difference(_SAFE_SENSITIVE_META_FIELDS))
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


def _json_str(
    value: JsonValue | None,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    stripped = value.strip()
    return stripped or None


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


def _canonical_sender_transfer_provenance(
    provenance: TransferProvenance,
) -> TransferProvenance:
    canonical = _transfer_provenance(provenance)
    for field_name in _RECEIVER_MANAGED_TRANSFER_PROVENANCE_FIELDS:
        canonical.pop(field_name, None)
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
    transfer_job.transfer_status = TransferJob.TransferStatus.INCONSISTENT
    transfer_job.processing_decision = TransferJob.ProcessingDecision.MARK_INCONSISTENT
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
    if transfer_job.resource_kind == TransferJob.ResourceKind.VIDEO.value:
        return _apply_video_transfer_metadata(transfer_job)
    if transfer_job.resource_kind == TransferJob.ResourceKind.REPORT.value:
        return _apply_report_transfer_metadata(transfer_job)

    transfer_job.transfer_status = TransferJob.TransferStatus.FAILED
    transfer_job.processing_decision = TransferJob.ProcessingDecision.REJECT_TRANSFER
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
    uploaded_file: UploadedFile,
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

    field_file = getattr(target, field_name)
    original_name = str(getattr(field_file, "name", "") or "")
    candidate_name = ""
    try:
        with prepare_inbound_hub_envelope(
            transfer_job=transfer_job,
            uploaded_file=uploaded_file,
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
                transfer_job.transfer_status = TransferJob.TransferStatus.INCONSISTENT
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

            with transaction.atomic():
                candidate_name = _store_model_stream(
                    instance=target,
                    field_name=field_name,
                    source=prepared.plaintext_stream,
                    stored_name=f"{expected_hash}{suffix}",
                )
                prepared.require_verified()

                if isinstance(target, VideoFile):
                    target.processed_video_hash = expected_hash
                    target.save(
                        update_fields=[
                            "processed_file",
                            "processed_video_hash",
                            "date_modified",
                        ]
                    )
                    _mark_video_transfer_as_processed(target)
                    media_type = "video"
                else:
                    target.save(update_fields=["processed_file", "date_modified"])
                    _mark_report_transfer_as_processed(target)
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
                )
                if original_name and original_name != candidate_name:
                    _delete_replaced_generation_after_commit(
                        instance=target,
                        field_name=field_name,
                        replaced_name=original_name,
                    )
                return applied_transfer_job
    except Exception:
        if candidate_name and candidate_name != original_name:
            candidate_field = getattr(target, field_name)
            candidate_field.name = candidate_name
            safe_delete_field_file(candidate_field, missing_ok=True)
            candidate_field.name = original_name
        raise


def _apply_video_transfer_metadata(transfer_job: TransferJob) -> TransferJob:
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    _assert_privacy_preserving_resource_rows(resource_rows)
    video_file_payload = _json_object(
        resource_rows.get("video_file") or {},
        field_name="resource_rows.video_file",
    )
    video_state_payload = _json_object(
        resource_rows.get("video_state") or {},
        field_name="resource_rows.video_state",
    )
    processing_history_payload = _json_object(
        resource_rows.get("processing_history") or {},
        field_name="resource_rows.processing_history",
    )
    processing_snapshot = cast(JsonObject, transfer_job.processing_snapshot or {})
    source_center = transfer_job.source_center

    if source_center is None:
        transfer_job.transfer_status = TransferJob.TransferStatus.FAILED
        transfer_job.processing_decision = (
            TransferJob.ProcessingDecision.REJECT_TRANSFER
        )
        transfer_job.status_detail = "No source_center could be resolved for transfer"
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
            .filter(video_hash=transfer_job.resource_hash)
            .first()
        )

        if video is None:
            video = VideoFile(
                video_hash=transfer_job.resource_hash,
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

        sensitive_meta_payload = resource_rows.get("sensitive_meta") or {}
        if isinstance(sensitive_meta_payload, dict) and sensitive_meta_payload:
            sensitive_meta = _upsert_sensitive_meta(
                existing=video.sensitive_meta if video.pk else None,
                payload=cast(JsonObject, sensitive_meta_payload),
                center=source_center,
            )
            video.sensitive_meta = sensitive_meta

        video.center = source_center
        _apply_video_file_payload(video, video_file_payload)

        video.save()
        video_state = get_or_create_video_state(video)
        _apply_video_state_payload(video_state, video_state_payload)

        processing_success = _coerce_optional_bool(
            processing_history_payload.get("success")
        )
        ProcessingHistory.get_or_create_for_hash(
            file_hash=transfer_job.resource_hash,
            obj=video,
            success=processing_success,
        )

        _apply_case_resolution_for_media(
            transfer_job=transfer_job,
            media_obj=video,
            media_type="video",
        )
        _apply_video_segment_rows(
            transfer_job=transfer_job,
            video=video,
            rows=_json_object_list(
                resource_rows.get("video_segments"),
                field_name="resource_rows.video_segments",
            ),
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
        _reconcile_segment_annotations_validated(
            video_state=video_state,
            sender_state_payload=video_state_payload,
        )

        processing_decision, transfer_status, status_detail = _decide_video_processing(
            transfer_job=transfer_job,
            video=video,
            processing_success=processing_success,
            processing_snapshot=processing_snapshot,
        )

        transfer_job.target_object_id = video.pk
        transfer_job.processing_decision = processing_decision
        transfer_job.transfer_status = transfer_status
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


def _apply_report_transfer_metadata(transfer_job: TransferJob) -> TransferJob:
    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    _assert_privacy_preserving_resource_rows(resource_rows)
    report_payload = _json_object(
        resource_rows.get("raw_pdf_file") or {},
        field_name="resource_rows.raw_pdf_file",
    )
    report_state_payload = _json_object(
        resource_rows.get("raw_pdf_state") or {},
        field_name="resource_rows.raw_pdf_state",
    )
    processing_history_payload = _json_object(
        resource_rows.get("processing_history") or {},
        field_name="resource_rows.processing_history",
    )
    source_center = transfer_job.source_center

    if source_center is None:
        transfer_job.transfer_status = TransferJob.TransferStatus.FAILED
        transfer_job.processing_decision = (
            TransferJob.ProcessingDecision.REJECT_TRANSFER
        )
        transfer_job.status_detail = "No source_center could be resolved for transfer"
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

        sensitive_meta_payload = resource_rows.get("sensitive_meta") or {}
        if isinstance(sensitive_meta_payload, dict) and sensitive_meta_payload:
            sensitive_meta = _upsert_sensitive_meta(
                existing=report.sensitive_meta if report.pk else None,
                payload=cast(JsonObject, sensitive_meta_payload),
                center=source_center,
            )
            report.sensitive_meta = sensitive_meta

        report.center = source_center
        _apply_report_file_payload(report, report_payload)

        report.save()
        report_state = get_or_create_raw_pdf_state(report)
        _apply_report_state_payload(report_state, report_state_payload)

        processing_success = _coerce_optional_bool(
            processing_history_payload.get("success")
        )
        ProcessingHistory.get_or_create_for_hash(
            file_hash=transfer_job.resource_hash,
            obj=report,
            success=processing_success,
        )

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

        processing_decision, transfer_status, status_detail = _decide_report_processing(
            transfer_job=transfer_job,
            report=report,
            processing_success=processing_success,
        )

        transfer_job.target_object_id = report.pk
        transfer_job.processing_decision = processing_decision
        transfer_job.transfer_status = transfer_status
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


def _attach_video_transfer_media(
    *,
    transfer_job: TransferJob,
    uploaded_file: UploadedFile,
    temp_path: Path,
    media_role: str,
) -> TransferJob:
    video = _get_transfer_video(transfer_job)
    upload_name = Path(str(getattr(uploaded_file, "name", "") or "upload.mp4")).name
    suffix = _normalized_suffix(upload_name, video.suffix or ".mp4")

    if media_role == "raw":
        actual_hash = sha256_file(temp_path)
        if actual_hash != transfer_job.resource_hash:
            raise ValueError(
                "Uploaded raw video hash does not match transfer resource_hash"
            )

        update_fields = _store_model_file(
            instance=video,
            field_name="raw_file",
            source_path=temp_path,
            stored_name=f"{transfer_job.resource_hash}{suffix}",
        )
        if video.suffix != suffix:
            video.suffix = suffix
            update_fields.append("suffix")
        if not video.original_file_name:
            video.original_file_name = upload_name
            update_fields.append("original_file_name")
        if update_fields:
            update_fields.append("date_modified")
            video.save(update_fields=update_fields)

        _record_media_upload(
            transfer_job=transfer_job,
            media_role=media_role,
            stored_name=_stored_field_name(video.raw_file),
            content_hash=actual_hash,
        )
        _apply_case_resolution_for_media(
            transfer_job=transfer_job,
            media_obj=video,
            media_type="video",
        )
        return _handle_video_processing_after_raw_upload(
            transfer_job=transfer_job,
            video=video,
            import_path=temp_path,
        )

    expected_hash = _expected_processed_video_hash(
        transfer_job=transfer_job, video=video
    )
    if not expected_hash:
        raise ValueError(
            "Processed video upload requires video_file.processed_video_hash in transfer metadata"
        )

    actual_hash = sha256_file(temp_path)
    if actual_hash != expected_hash:
        raise ValueError(
            "Uploaded processed video hash does not match the expected processed_video_hash"
        )

    update_fields = _store_model_file(
        instance=video,
        field_name="processed_file",
        source_path=temp_path,
        stored_name=f"{expected_hash}{suffix}",
    )
    if video.processed_video_hash != expected_hash:
        video.processed_video_hash = expected_hash
        update_fields.append("processed_video_hash")
    if update_fields:
        update_fields.append("date_modified")
        video.save(update_fields=update_fields)

    _mark_video_transfer_as_processed(video)
    _record_media_upload(
        transfer_job=transfer_job,
        media_role=media_role,
        stored_name=_stored_field_name(video.processed_file),
        content_hash=actual_hash,
    )
    _apply_case_resolution_for_media(
        transfer_job=transfer_job,
        media_obj=video,
        media_type="video",
    )
    return _save_transfer_job_state(
        transfer_job=transfer_job,
        target_object_id=video.pk,
        transfer_status=TransferJob.TransferStatus.APPLIED,
        processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
        status_detail="Processed video uploaded and sender processing state preserved",
    )


def _attach_report_transfer_media(
    *,
    transfer_job: TransferJob,
    uploaded_file: UploadedFile,
    temp_path: Path,
    media_role: str,
) -> TransferJob:
    report = _get_transfer_report(transfer_job)

    if media_role == "raw":
        actual_hash = get_pdf_hash(temp_path)
        if actual_hash != transfer_job.resource_hash:
            raise ValueError(
                "Uploaded raw report hash does not match transfer resource_hash"
            )

        update_fields = _store_model_file(
            instance=report,
            field_name="file",
            source_path=temp_path,
            stored_name=f"{transfer_job.resource_hash}.pdf",
        )
        if update_fields:
            update_fields.append("date_modified")
            report.save(update_fields=update_fields)

        _record_media_upload(
            transfer_job=transfer_job,
            media_role=media_role,
            stored_name=_stored_field_name(report.file),
            content_hash=actual_hash,
        )
        _apply_case_resolution_for_media(
            transfer_job=transfer_job,
            media_obj=report,
            media_type="pdf",
        )
        return _handle_report_processing_after_raw_upload(
            transfer_job=transfer_job,
            report=report,
            import_path=temp_path,
        )

    expected_hash = _expected_processed_report_hash(transfer_job=transfer_job)
    if not expected_hash:
        raise ValueError(
            "Processed report upload requires "
            "raw_pdf_state.processed_file_sha256 in transfer metadata"
        )
    actual_hash = get_pdf_hash(temp_path)
    if actual_hash != expected_hash:
        raise ValueError(
            "Uploaded processed report hash does not match the expected "
            "processed_file_sha256"
        )
    update_fields = _store_model_file(
        instance=report,
        field_name="processed_file",
        source_path=temp_path,
        stored_name=f"{expected_hash}.pdf",
    )
    if update_fields:
        update_fields.append("date_modified")
        report.save(update_fields=update_fields)

    _mark_report_transfer_as_processed(report)
    _record_media_upload(
        transfer_job=transfer_job,
        media_role=media_role,
        stored_name=_stored_field_name(report.processed_file),
        content_hash=actual_hash,
    )
    _apply_case_resolution_for_media(
        transfer_job=transfer_job,
        media_obj=report,
        media_type="pdf",
    )
    return _save_transfer_job_state(
        transfer_job=transfer_job,
        target_object_id=report.pk,
        transfer_status=TransferJob.TransferStatus.APPLIED,
        processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
        status_detail="Processed report uploaded and sender processing state preserved",
    )


def _handle_video_processing_after_raw_upload(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
    import_path: Path,
) -> TransferJob:
    sender_success = _sender_processing_success(transfer_job)
    processed_file = getattr(video, "processed_file", None)
    local_processed_present = file_exists(processed_file)
    if (
        not local_processed_present
        and getattr(processed_file, "name", None)
        and not hasattr(processed_file, "storage")
    ):
        local_processed_present = True

    if (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.INGEST_ONLY_NO_PROCESSING.value
    ):
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=video.pk,
            transfer_status=TransferJob.TransferStatus.APPLIED,
            processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
            status_detail="Raw video uploaded and stored without starting processing",
        )

    if (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value
        and sender_success
    ):
        if local_processed_present:
            _mark_video_transfer_as_processed(video)
            return _save_transfer_job_state(
                transfer_job=transfer_job,
                target_object_id=video.pk,
                transfer_status=TransferJob.TransferStatus.APPLIED,
                processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
                status_detail=(
                    "Raw video uploaded; existing processed artifact preserves sender state"
                ),
            )
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=video.pk,
            transfer_status=TransferJob.TransferStatus.AWAITING_MEDIA,
            processing_decision=TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
            status_detail=(
                "Raw video uploaded; waiting for processed media to preserve sender state"
            ),
        )

    should_process = False
    if (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.REPROCESS_ALWAYS.value
    ):
        should_process = True
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS.value
    ):
        should_process = not local_processed_present
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value
    ):
        should_process = not bool(sender_success)

    if not should_process:
        decision = TransferJob.ProcessingDecision.SKIP_EXISTING_SUCCESS
        detail = "Raw video uploaded; local processed output already exists"
        if not local_processed_present:
            decision = TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE
            detail = "Raw video uploaded without processing due to transfer policy"
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=video.pk,
            transfer_status=TransferJob.TransferStatus.APPLIED,
            processing_decision=decision,
            status_detail=detail,
        )

    processor_name = _default_processor_name()
    if not processor_name:
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=video.pk,
            transfer_status=TransferJob.TransferStatus.FAILED,
            processing_decision=TransferJob.ProcessingDecision.START_PROCESSING,
            status_detail="No default EndoscopyProcessor is configured for video processing",
        )

    try:
        from endoreg_db.import_files.video_import_service import VideoImportService

        VideoImportService().import_and_anonymize(
            file_path=import_path,
            center_name=video.center.name,
            processor_name=processor_name,
            retry=True,
        )
    except Exception as exc:
        logger.exception(
            "Hub video transfer processing failed for %s", transfer_job.transfer_key
        )
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=video.pk,
            transfer_status=TransferJob.TransferStatus.FAILED,
            processing_decision=TransferJob.ProcessingDecision.START_PROCESSING,
            status_detail=f"Raw video uploaded but processing failed: {exc}",
        )

    video.refresh_from_db()
    _apply_case_resolution_for_media(
        transfer_job=transfer_job,
        media_obj=video,
        media_type="video",
    )
    return _save_transfer_job_state(
        transfer_job=transfer_job,
        target_object_id=video.pk,
        transfer_status=TransferJob.TransferStatus.APPLIED,
        processing_decision=TransferJob.ProcessingDecision.START_PROCESSING,
        status_detail="Raw video uploaded and processing completed on the hub",
    )


def _handle_report_processing_after_raw_upload(
    *,
    transfer_job: TransferJob,
    report: RawPdfFile,
    import_path: Path,
) -> TransferJob:
    sender_success = _sender_processing_success(transfer_job)
    local_processed_present = report.anonymized_file_path is not None

    if (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.INGEST_ONLY_NO_PROCESSING.value
    ):
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=report.pk,
            transfer_status=TransferJob.TransferStatus.APPLIED,
            processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
            status_detail="Raw report uploaded and stored without starting processing",
        )

    if (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value
        and sender_success
    ):
        if local_processed_present:
            _mark_report_transfer_as_processed(report)
            return _save_transfer_job_state(
                transfer_job=transfer_job,
                target_object_id=report.pk,
                transfer_status=TransferJob.TransferStatus.APPLIED,
                processing_decision=TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE,
                status_detail=(
                    "Raw report uploaded; existing processed artifact preserves sender state"
                ),
            )
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=report.pk,
            transfer_status=TransferJob.TransferStatus.AWAITING_MEDIA,
            processing_decision=TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
            status_detail=(
                "Raw report uploaded; waiting for processed media to preserve sender state"
            ),
        )

    should_process = False
    if (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.REPROCESS_ALWAYS.value
    ):
        should_process = True
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS.value
    ):
        should_process = not local_processed_present
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value
    ):
        should_process = not bool(sender_success)

    if not should_process:
        decision = TransferJob.ProcessingDecision.SKIP_EXISTING_SUCCESS
        detail = "Raw report uploaded; local processed output already exists"
        if not local_processed_present:
            decision = TransferJob.ProcessingDecision.SKIP_PRESERVED_STATE
            detail = "Raw report uploaded without processing due to transfer policy"
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=report.pk,
            transfer_status=TransferJob.TransferStatus.APPLIED,
            processing_decision=decision,
            status_detail=detail,
        )

    try:
        from endoreg_db.import_files.report_import_service import ReportImportService

        ReportImportService().import_and_anonymize(
            file_path=import_path,
            center_name=report.center.name if report.center is not None else "",
            retry=True,
        )
    except Exception as exc:
        logger.exception(
            "Hub report transfer processing failed for %s", transfer_job.transfer_key
        )
        return _save_transfer_job_state(
            transfer_job=transfer_job,
            target_object_id=report.pk,
            transfer_status=TransferJob.TransferStatus.FAILED,
            processing_decision=TransferJob.ProcessingDecision.START_PROCESSING,
            status_detail=f"Raw report uploaded but processing failed: {exc}",
        )

    report.refresh_from_db()
    _apply_case_resolution_for_media(
        transfer_job=transfer_job,
        media_obj=report,
        media_type="pdf",
    )
    return _save_transfer_job_state(
        transfer_job=transfer_job,
        target_object_id=report.pk,
        transfer_status=TransferJob.TransferStatus.APPLIED,
        processing_decision=TransferJob.ProcessingDecision.START_PROCESSING,
        status_detail="Raw report uploaded and processing completed on the hub",
    )


def _save_transfer_job_state(
    *,
    transfer_job: TransferJob,
    target_object_id: int | None,
    transfer_status: str,
    processing_decision: str,
    status_detail: str,
) -> TransferJob:
    transfer_job.target_object_id = target_object_id
    transfer_job.transfer_status = transfer_status
    transfer_job.processing_decision = processing_decision
    transfer_job.status_detail = status_detail
    transfer_job.save(
        update_fields=[
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
    )
    return transfer_job


def _store_model_file(
    *,
    instance: Any,
    field_name: str,
    source_path: Path,
    stored_name: str,
) -> list[str]:
    delete_field_file(instance, field_name, missing_ok=True, save=False)
    field_file = getattr(instance, field_name)
    save_local_file(field_file, source_path, name=stored_name, save=False)
    return [field_name]


def _store_model_stream(
    *,
    instance: Any,
    field_name: str,
    source: BinaryIO,
    stored_name: str,
) -> str:
    """Publish a stream as a new storage generation without deleting the old one."""

    field_file = getattr(instance, field_name)
    storage_name = field_file.field.generate_filename(instance, stored_name)
    django_file = File(source, name=stored_name)
    saved_name = str(field_file.storage.save(storage_name, django_file))
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


def _stored_field_name(field_file: object) -> str:
    stored_name = getattr(field_file, "name", None)
    if not isinstance(stored_name, str) or not stored_name:
        raise RuntimeError("Stored media field is missing a storage name")
    return stored_name


def _write_uploaded_file_to_temp(
    *, uploaded_file: UploadedFile, default_suffix: str
) -> Path:
    ensure_directory(TRANSCODING_DIR)
    upload_name = Path(str(getattr(uploaded_file, "name", "") or "upload")).name
    suffix = _normalized_suffix(upload_name, default_suffix)
    destination = TRANSCODING_DIR / f"hub-upload-{uuid.uuid4().hex}{suffix}"
    content = (
        cast(Iterable[bytes], uploaded_file.chunks())
        if hasattr(uploaded_file, "chunks")
        else [uploaded_file.read()]
    )
    required_bytes = uploaded_file.size
    if required_bytes is None or required_bytes < 0:
        raise ValueError("Uploaded media size must be known before staging.")
    return atomic_handoff_file(
        destination=destination,
        content=content,
        required_bytes=required_bytes,
        file_mode=0o600,
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
        video = queryset.filter(video_hash=transfer_job.resource_hash).first()
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


def _mark_video_transfer_as_processed(video: VideoFile) -> None:
    state = get_or_create_video_state(video)
    state.processing_started = True
    state.anonymized = True
    state.sensitive_meta_processed = True
    state.anonymization_validated = True
    state.processed_file_sha256 = str(video.processed_video_hash or "").strip()
    state.save(
        update_fields=[
            "processing_started",
            "anonymized",
            "sensitive_meta_processed",
            "anonymization_validated",
            "processed_file_sha256",
            "date_modified",
        ]
    )
    ProcessingHistory.mark_success(file_hash=video.video_hash, obj=video)


def _mark_report_transfer_as_processed(report: RawPdfFile) -> None:
    state = get_or_create_raw_pdf_state(report)
    actual_hash = sha256_file(report.processed_file)
    state.processing_started = True
    state.anonymized = True
    state.sensitive_meta_processed = True
    state.anonymization_validated = True
    state.processed_file_sha256 = actual_hash
    state.save(
        update_fields=[
            "processing_started",
            "anonymized",
            "sensitive_meta_processed",
            "anonymization_validated",
            "processed_file_sha256",
            "date_modified",
        ]
    )
    ProcessingHistory.mark_success(file_hash=report.pdf_hash, obj=report)


def _sender_processing_success(transfer_job: TransferJob) -> bool | None:
    processing_snapshot = cast(JsonObject, transfer_job.processing_snapshot or {})
    sender_processing_success = _coerce_optional_bool(
        processing_snapshot.get("sender_processing_success")
    )
    if sender_processing_success is not None:
        return sender_processing_success

    resource_rows = cast(JsonObject, transfer_job.resource_rows or {})
    processing_history_payload = resource_rows.get("processing_history") or {}
    if isinstance(processing_history_payload, dict):
        return _coerce_optional_bool(processing_history_payload.get("success"))
    return None


def _normalized_suffix(upload_name: str, default_suffix: str) -> str:
    suffix = Path(upload_name).suffix.strip() or default_suffix
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return suffix.lower()


def _apply_frame_annotation_rows(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
    rows: list[JsonObject],
) -> None:
    if not rows:
        return

    for row in rows:
        row_video_hash = str(row.get("video_hash") or "").strip()
        if row_video_hash and row_video_hash != video.video_hash:
            raise ValueError(
                "resource_rows.frame_annotations video_hash does not match transfer video"
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

        frame, _ = Frame.objects.get_or_create(
            video=video,
            frame_number=frame_number,
            defaults={
                "relative_path": relative_path,
                "timestamp": frame_timestamp,
            },
        )
        frame_update_fields: list[str] = []
        if frame.relative_path != relative_path:
            frame.relative_path = relative_path
            frame_update_fields.append("relative_path")
        if frame.timestamp != frame_timestamp:
            frame.timestamp = frame_timestamp
            frame_update_fields.append("timestamp")
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
        video_state.frame_annotations_generated = True
        video_state.save(update_fields=["frame_annotations_generated", "date_modified"])


def _apply_video_segment_rows(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
    rows: list[JsonObject],
) -> None:
    if not rows:
        return
    frame_count = video.frame_count
    if frame_count is None or frame_count <= 0:
        raise ValueError(
            "resource_rows.video_file.frame_count is required for video segments"
        )

    with suppress_label_video_segment_state_side_effects():
        for row in rows:
            _upsert_video_segment_row(
                transfer_job=transfer_job,
                video=video,
                frame_count=frame_count,
                row=row,
            )


def _upsert_video_segment_row(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
    frame_count: int,
    row: JsonObject,
) -> None:
    source_node_key = str(row["source_node_key"]).strip()
    if source_node_key != transfer_job.source_node.node_key:
        raise ValueError(
            "resource_rows.video_segments source_node_key does not match transfer"
        )
    row_video_hash = str(row["video_hash"]).strip()
    if row_video_hash != video.video_hash:
        raise ValueError(
            "resource_rows.video_segments video_hash does not match transfer video"
        )

    source_segment_id = str(row["source_segment_id"]).strip()
    start_frame_number = _json_int(
        row["start_frame_number"],
        field_name="resource_rows.video_segments.start_frame_number",
    )
    end_frame_number = _json_int(
        row["end_frame_number_exclusive"],
        field_name="resource_rows.video_segments.end_frame_number_exclusive",
    )
    if start_frame_number < 0 or end_frame_number <= start_frame_number:
        raise ValueError("video segment frame range must be non-empty and non-negative")
    if end_frame_number > frame_count:
        raise ValueError("video segment exclusive end exceeds video frame_count")

    label_name = str(row["label_name"]).strip()
    label, _ = Label.get_or_create_from_name(label_name)
    provenance = _json_object(
        row["anonymous_provenance"],
        field_name="resource_rows.video_segments.anonymous_provenance",
    )
    information_source_name = str(provenance["information_source_name"]).strip()
    information_source, _ = cast(
        InformationSourceManager, InformationSource.objects
    ).get_or_create_by_name(
        information_source_name,
        description="Anonymous provenance imported from a hub transfer segment",
    )
    source_kind = str(row["source_kind"])
    prediction_meta = _resolve_transferred_segment_prediction_meta(
        video=video,
        label=label,
        source_kind=source_kind,
        model_name=_json_str(
            row.get("model_name"),
            field_name="resource_rows.video_segments.model_name",
        ),
        model_version=_json_str(
            row.get("model_version"),
            field_name="resource_rows.video_segments.model_version",
        ),
    )

    segment = (
        LabelVideoSegment.objects.select_related("video_file")
        .filter(
            source_node_key=source_node_key,
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
            source_node_key=source_node_key,
            source_segment_id=source_segment_id,
            video_file=video,
        )

    segment.video_file = video
    segment.start_frame_number = start_frame_number
    segment.end_frame_number = end_frame_number
    segment.label = label
    segment.source = information_source
    segment.prediction_meta = prediction_meta
    segment.export_segment = _json_bool(
        row["export_segment"],
        field_name="resource_rows.video_segments.export_segment",
    )
    segment.save()

    state = LabelVideoSegmentState.objects.filter(origin=segment).first()
    if state is None:
        state = LabelVideoSegmentState(origin=segment)
        state.prediction = source_kind == "prediction"
        state.annotation = source_kind == "manual_annotation"
        state.is_validated = row["validation_state"] == "validated"
        models.Model.save(state)
    else:
        state.prediction = source_kind == "prediction"
        state.annotation = source_kind == "manual_annotation"
        state.is_validated = row["validation_state"] == "validated"
        models.Model.save(
            state,
            update_fields=["prediction", "annotation", "is_validated"],
        )


def _resolve_transferred_segment_prediction_meta(
    *,
    video: VideoFile,
    label: Label,
    source_kind: str,
    model_name: str | None,
    model_version: str | None,
) -> VideoPredictionMeta | None:
    if source_kind not in {"manual_annotation", "prediction"}:
        raise ValueError(f"Unsupported video segment source_kind: {source_kind!r}")
    if model_name is None and model_version is None:
        return None
    if source_kind != "prediction" or model_name is None or model_version is None:
        raise ValueError(
            "model_name and model_version are permitted only together for predictions"
        )
    candidates = list(
        ModelMeta.objects.filter(
            name=model_name,
            version=model_version,
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
    video_state.segment_annotations_validated = _json_bool(
        sender_state_payload["segment_annotations_validated"],
        field_name="resource_rows.video_state.segment_annotations_validated",
    )
    video_state.save(update_fields=["segment_annotations_validated", "date_modified"])


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

    report = (
        PatientExaminationReport.objects.filter(
            patient_examination=patient_examination,
            template_name=template_name,
            template_version=template_version,
            template_hash=template_hash,
            version=version,
        )
        .order_by("-id")
        .first()
    )
    if report is None:
        report = PatientExaminationReport(
            patient_examination=patient_examination,
            template_name=template_name,
            template_version=template_version,
            template_hash=template_hash,
            version=version,
        )

    report_any = cast(Any, report)
    report_any.patient_examination = patient_examination
    report_any.template_name = template_name
    report_any.template_version = template_version
    report_any.template_hash = template_hash
    report_any.version = version
    report_any.status = PatientExaminationReport.Status.FINAL.value
    report_any.is_active = True

    report_any.save()


def _apply_video_file_payload(video: VideoFile, payload: JsonObject) -> None:
    sync_fields = [
        "processed_video_hash",
        "fps",
        "duration",
        "frame_count",
        "width",
        "height",
        "suffix",
    ]
    for field_name in sync_fields:
        if field_name in payload:
            setattr(video, field_name, cast(Any, payload[field_name]))


def _apply_video_state_payload(video_state: VideoState, payload: JsonObject) -> None:
    sync_fields = [
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
    ]
    updated_fields: list[str] = []
    for field_name in sync_fields:
        if field_name in payload:
            setattr(video_state, field_name, cast(Any, payload[field_name]))
            updated_fields.append(field_name)
    if updated_fields:
        updated_fields.append("date_modified")
        video_state.save(update_fields=updated_fields)


def _apply_report_file_payload(report: RawPdfFile, payload: JsonObject) -> None:
    sync_fields = [
        "anonymized_text",
        "state_report_processing_required",
        "state_report_processed",
    ]
    for field_name in sync_fields:
        if field_name in payload:
            setattr(report, field_name, cast(Any, payload[field_name]))


def _apply_report_state_payload(report_state: RawPdfState, payload: JsonObject) -> None:
    sync_fields = [
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
    ]
    updated_fields: list[str] = []
    for field_name in sync_fields:
        if field_name in payload:
            setattr(report_state, field_name, cast(Any, payload[field_name]))
            updated_fields.append(field_name)
    if updated_fields:
        updated_fields.append("date_modified")
        report_state.save(update_fields=updated_fields)


def _upsert_sensitive_meta(
    *,
    existing: SensitiveMeta | None,
    payload: JsonObject,
    center: Center,
) -> SensitiveMeta:
    _assert_privacy_preserving_resource_rows(
        cast(JsonObject, {"sensitive_meta": payload})
    )
    payload_patient_hash = str(payload.get("patient_hash", "") or "").strip()
    payload_examination_hash = str(payload.get("examination_hash", "") or "").strip()
    if not payload_patient_hash or not payload_examination_hash:
        raise ValueError(
            "Hub sensitive metadata requires patient_hash and examination_hash."
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

    patient = Patient.objects.filter(patient_hash=payload_patient_hash).first()
    if patient is None:
        patient = Patient.objects.create(
            first_name="",
            last_name="",
            dob=None,
            gender=None,
            center=center,
            patient_hash=payload_patient_hash,
            is_real_person=False,
        )
    patient_examination = (
        PatientExamination.objects.select_related("patient")
        .filter(hash=payload_examination_hash)
        .first()
    )
    if patient_examination is not None and (
        patient_examination.patient.patient_hash != payload_patient_hash
    ):
        raise ValueError(
            "Hub examination_hash is already linked to a different patient_hash."
        )
    if patient_examination is None:
        patient_examination = PatientExamination.objects.create(
            patient=patient,
            examination=None,
            hash=payload_examination_hash,
        )
    SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
        pseudo_patient=patient,
        pseudo_examination=patient_examination,
    )
    sensitive_meta.pseudo_patient = patient
    sensitive_meta.pseudo_examination = patient_examination
    return sensitive_meta


def _apply_case_resolution_for_media(
    *,
    transfer_job: TransferJob,
    media_obj: RawPdfFile | VideoFile,
    media_type: Literal["video", "pdf"],
) -> None:
    if getattr(media_obj, "sensitive_meta", None) is None:
        transfer_job.case_resolution_status = (
            TransferJob.CaseResolutionStatus.UNRESOLVED
        )
        transfer_job.linked_patient_id = None
        transfer_job.linked_patient_examination_id = None
        return

    sensitive_meta = media_obj.sensitive_meta
    if not sensitive_meta:
        transfer_job.case_resolution_status = (
            TransferJob.CaseResolutionStatus.UNRESOLVED
        )
        transfer_job.linked_patient_id = None
        transfer_job.linked_patient_examination_id = None
        return

    if not getattr(sensitive_meta, "patient_hash", None) or not getattr(
        sensitive_meta, "examination_hash", None
    ):
        transfer_job.case_resolution_status = (
            TransferJob.CaseResolutionStatus.UNRESOLVED
        )
        transfer_job.linked_patient_id = None
        transfer_job.linked_patient_examination_id = None
        return

    resolution = auto_resolve_media_case(media_type=media_type, media_obj=media_obj)
    transfer_job.case_resolution_status = resolution.status
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


def _decide_video_processing(
    *,
    transfer_job: TransferJob,
    video: VideoFile,
    processing_success: bool | None,
    processing_snapshot: JsonObject,
) -> tuple[str, str, str]:
    local_history_success = ProcessingHistory.has_history_for_hash(
        file_hash=transfer_job.resource_hash,
        success=True,
    )
    local_raw_present = file_exists(video.raw_file)
    local_processed_present = file_exists(video.processed_file)
    sender_processing_success = _coerce_optional_bool(
        processing_snapshot.get("sender_processing_success")
    )
    if sender_processing_success is None:
        sender_processing_success = processing_success

    if local_history_success and (local_processed_present or local_raw_present):
        return (
            TransferJob.ProcessingDecision.SKIP_EXISTING_SUCCESS,
            TransferJob.TransferStatus.APPLIED,
            "Existing local processing history and artifacts allow replay suppression",
        )

    if transfer_job.transfer_mode == TransferJob.TransferMode.METADATA_ONLY.value:
        if (
            transfer_job.processing_policy
            == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value
            and sender_processing_success
            and not local_processed_present
            and not local_raw_present
        ):
            return (
                TransferJob.ProcessingDecision.MARK_INCONSISTENT,
                TransferJob.TransferStatus.INCONSISTENT,
                "Sender claims completed processing but no local artifacts are available yet",
            )
        return (
            TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
            TransferJob.TransferStatus.AWAITING_MEDIA,
            "Metadata synchronized; media transfer is still required before processing can start",
        )

    return (
        TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
        TransferJob.TransferStatus.AWAITING_MEDIA,
        "Metadata synchronized; awaiting uploaded video media for the transfer job",
    )


def _decide_report_processing(
    *,
    transfer_job: TransferJob,
    report: RawPdfFile,
    processing_success: bool | None,
) -> tuple[str, str, str]:
    local_history_success = ProcessingHistory.has_history_for_hash(
        file_hash=transfer_job.resource_hash,
        success=True,
    )
    local_raw_present = file_exists(report.file)
    local_processed_present = file_exists(report.processed_file)

    if local_history_success and (local_raw_present or local_processed_present):
        return (
            TransferJob.ProcessingDecision.SKIP_EXISTING_SUCCESS,
            TransferJob.TransferStatus.APPLIED,
            "Existing local processing history and artifacts allow replay suppression",
        )

    if transfer_job.transfer_mode == TransferJob.TransferMode.METADATA_ONLY.value:
        if (
            transfer_job.processing_policy
            == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE.value
            and processing_success
            and not local_raw_present
            and not local_processed_present
        ):
            return (
                TransferJob.ProcessingDecision.MARK_INCONSISTENT,
                TransferJob.TransferStatus.INCONSISTENT,
                "Sender claims completed processing but no local report artifacts are available yet",
            )
        return (
            TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
            TransferJob.TransferStatus.AWAITING_MEDIA,
            "Metadata synchronized; media transfer is still required before processing can start",
        )

    return (
        TransferJob.ProcessingDecision.WAIT_FOR_MISSING_MEDIA,
        TransferJob.TransferStatus.AWAITING_MEDIA,
        "Metadata synchronized; awaiting uploaded report media for the transfer job",
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
