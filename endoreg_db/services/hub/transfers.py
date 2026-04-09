from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal, NotRequired, TypedDict, cast

from django.db import transaction
from django.utils import timezone

from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.import_files.video_import_service import VideoImportService
from endoreg_db.models import (
    Center,
    NetworkNode,
    RawPdfFile,
    RawPdfState,
    SensitiveMeta,
    TransferJob,
    VideoFile,
    VideoState,
)
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.models.metadata import sensitive_meta_logic
from endoreg_db.services.auto_case_resolution import auto_resolve_media_case
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.utils.file_operations import safe_unlink_file, sha256_file
from endoreg_db.utils.hashs import get_pdf_hash
from endoreg_db.utils.paths import TRANSCODING_DIR
from endoreg_db.utils.storage import delete_field_file, save_local_file
from .ingest import _default_processor_name

logger = logging.getLogger(__name__)


class TransferProvenance(TypedDict, total=False):
    entrypoint: str
    source_node_key: str
    target_node_key: str
    source_center_key: str | None
    transfer_mode: str
    processing_policy: str
    cleanup_policy: str
    media_uploads: list[dict[str, str]]
    case_resolution: dict[str, Any]
    custom_marker: NotRequired[str]


def _transfer_provenance(
    existing: TransferProvenance | None = None,
) -> TransferProvenance:
    provenance: TransferProvenance = {}
    if existing:
        provenance.update(existing)
    return provenance


def _update_transfer_provenance(
    transfer_job: TransferJob,
    **updates: object,
) -> TransferProvenance:
    provenance = _transfer_provenance(transfer_job.provenance)
    for key, value in updates.items():
        if value is not None:
            cast(Any, provenance)[key] = value
    transfer_job.provenance = provenance
    return provenance


def _normalize_sensitive_meta_value(field_name: str, value: Any) -> Any:
    if field_name == "patient_dob":
        if isinstance(value, str):
            parsed = sensitive_meta_logic.parse_any_date(value)
            if parsed is None:
                logger.warning(
                    "Skipping invalid hub transfer patient_dob value %r", value
                )
                return None
            return timezone.make_aware(datetime.combine(parsed, datetime.min.time()))
        return value

    if field_name == "examination_date" and isinstance(value, str):
        parsed = sensitive_meta_logic.parse_any_date(value)
        if parsed is None:
            logger.warning(
                "Skipping invalid hub transfer examination_date value %r", value
            )
            return None
        return parsed

    return value


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
    resource_rows: dict[str, Any],
    processing_snapshot: dict[str, Any],
    provenance: TransferProvenance,
    created_by=None,
) -> tuple[TransferJob, bool]:
    transfer_job_manager = cast(Any, TransferJob.objects)
    existing = transfer_job_manager.filter(transfer_key=transfer_key).first()
    if existing is not None:
        if (
            existing.source_node_id != source_node.id
            or existing.target_node_id != target_node.id
            or existing.resource_kind != resource_kind
            or existing.resource_hash != resource_hash
        ):
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
        provenance=_normalized_transfer_provenance(
            provenance=provenance,
            source_node=source_node,
            target_node=target_node,
            source_center=source_center,
            transfer_mode=transfer_mode,
            processing_policy=processing_policy,
            cleanup_policy=cleanup_policy,
        ),
        cleanup_status=(
            TransferJob.CleanupStatus.NOT_REQUESTED
            if cleanup_policy == TransferJob.CleanupPolicy.RETAIN_ALL
            else TransferJob.CleanupStatus.DEFERRED
        ),
        created_by=created_by
        if getattr(created_by, "is_authenticated", False)
        else None,
    )
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
        return None

    normalized_key = str(provided_node_key or "").strip()
    normalized_secret = str(provided_secret or "").strip()

    if source_node.shared_secret_hash:
        if normalized_key != source_node.node_key:
            return None
        if not source_node.check_shared_secret(normalized_secret):
            return None
        return source_node

    if normalized_key and normalized_key != source_node.node_key:
        return None
    return source_node


def apply_transfer_metadata(transfer_job: TransferJob) -> TransferJob:
    if transfer_job.resource_kind == TransferJob.ResourceKind.VIDEO:
        return _apply_video_transfer_metadata(transfer_job)
    if transfer_job.resource_kind == TransferJob.ResourceKind.REPORT:
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
    uploaded_file,
    media_role: str,
) -> TransferJob:
    if media_role not in {"raw", "processed"}:
        raise ValueError("media_role must be either 'raw' or 'processed'")

    default_suffix = ".mp4"
    if transfer_job.resource_kind == TransferJob.ResourceKind.REPORT:
        default_suffix = ".pdf"

    temp_path = _write_uploaded_file_to_temp(
        uploaded_file=uploaded_file,
        default_suffix=default_suffix,
    )
    try:
        if transfer_job.resource_kind == TransferJob.ResourceKind.VIDEO:
            return _attach_video_transfer_media(
                transfer_job=transfer_job,
                uploaded_file=uploaded_file,
                temp_path=temp_path,
                media_role=media_role,
            )
        if transfer_job.resource_kind == TransferJob.ResourceKind.REPORT:
            return _attach_report_transfer_media(
                transfer_job=transfer_job,
                uploaded_file=uploaded_file,
                temp_path=temp_path,
                media_role=media_role,
            )
        raise ValueError(f"Unsupported resource_kind: {transfer_job.resource_kind}")
    finally:
        safe_unlink_file(temp_path, missing_ok=True)


def _apply_video_transfer_metadata(transfer_job: TransferJob) -> TransferJob:
    resource_rows = transfer_job.resource_rows or {}
    video_file_payload = resource_rows.get("video_file") or {}
    video_state_payload = resource_rows.get("video_state") or {}
    processing_history_payload = resource_rows.get("processing_history") or {}
    processing_snapshot = transfer_job.processing_snapshot or {}
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
            VideoFile.objects.select_related("state", "sensitive_meta")
            .filter(video_hash=transfer_job.resource_hash)
            .first()
        )

        if video is None:
            video = VideoFile(
                video_hash=transfer_job.resource_hash,
                center=source_center,
            )

        sensitive_meta_payload = resource_rows.get("sensitive_meta") or {}
        if isinstance(sensitive_meta_payload, dict) and sensitive_meta_payload:
            sensitive_meta = _upsert_sensitive_meta(
                existing=video.sensitive_meta if video.pk else None,
                payload=sensitive_meta_payload,
                center=source_center,
            )
            video.sensitive_meta = sensitive_meta

        video.center = source_center
        _apply_video_file_payload(video, video_file_payload)

        video.save()
        video_state = video.get_or_create_state()
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
    resource_rows = transfer_job.resource_rows or {}
    report_payload = resource_rows.get("raw_pdf_file") or {}
    report_state_payload = resource_rows.get("raw_pdf_state") or {}
    processing_history_payload = resource_rows.get("processing_history") or {}
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
            RawPdfFile.objects.select_related("state", "sensitive_meta")
            .filter(pdf_hash=transfer_job.resource_hash)
            .first()
        )
        if report is None:
            report = RawPdfFile(
                pdf_hash=transfer_job.resource_hash,
                center=source_center,
            )

        sensitive_meta_payload = resource_rows.get("sensitive_meta") or {}
        if isinstance(sensitive_meta_payload, dict) and sensitive_meta_payload:
            sensitive_meta = _upsert_sensitive_meta(
                existing=report.sensitive_meta if report.pk else None,
                payload=sensitive_meta_payload,
                center=source_center,
            )
            report.sensitive_meta = sensitive_meta

        report.center = source_center
        _apply_report_file_payload(report, report_payload)

        report.save()
        report_state = report.get_or_create_state()
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
    uploaded_file,
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
            stored_name=video.raw_file.name,
            content_hash=actual_hash,
            uploaded_name=upload_name,
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
        stored_name=video.processed_file.name,
        content_hash=actual_hash,
        uploaded_name=upload_name,
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
    uploaded_file,
    temp_path: Path,
    media_role: str,
) -> TransferJob:
    report = _get_transfer_report(transfer_job)
    upload_name = Path(str(getattr(uploaded_file, "name", "") or "upload.pdf")).name

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
            stored_name=report.file.name,
            content_hash=actual_hash,
            uploaded_name=upload_name,
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

    actual_hash = get_pdf_hash(temp_path)
    update_fields = _store_model_file(
        instance=report,
        field_name="processed_file",
        source_path=temp_path,
        stored_name=f"{transfer_job.resource_hash}_processed.pdf",
    )
    if update_fields:
        update_fields.append("date_modified")
        report.save(update_fields=update_fields)

    _mark_report_transfer_as_processed(report)
    _record_media_upload(
        transfer_job=transfer_job,
        media_role=media_role,
        stored_name=report.processed_file.name,
        content_hash=actual_hash,
        uploaded_name=upload_name,
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
    local_processed_present = video.get_processed_file_path() is not None

    if (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.INGEST_ONLY_NO_PROCESSING
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
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE
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
    if transfer_job.processing_policy == TransferJob.ProcessingPolicy.REPROCESS_ALWAYS:
        should_process = True
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS
    ):
        should_process = not local_processed_present
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE
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
        == TransferJob.ProcessingPolicy.INGEST_ONLY_NO_PROCESSING
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
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE
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
    if transfer_job.processing_policy == TransferJob.ProcessingPolicy.REPROCESS_ALWAYS:
        should_process = True
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.REPROCESS_IF_MISSING_OUTPUTS
    ):
        should_process = not local_processed_present
    elif (
        transfer_job.processing_policy
        == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE
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
    instance,
    field_name: str,
    source_path: Path,
    stored_name: str,
) -> list[str]:
    field_file = getattr(instance, field_name)
    delete_field_file(field_file, missing_ok=True, save=False)
    save_local_file(field_file, source_path, name=stored_name, save=False)
    return [field_name]


def _write_uploaded_file_to_temp(*, uploaded_file, default_suffix: str) -> Path:
    TRANSCODING_DIR.mkdir(parents=True, exist_ok=True)
    upload_name = Path(str(getattr(uploaded_file, "name", "") or "upload")).name
    suffix = _normalized_suffix(upload_name, default_suffix)
    with NamedTemporaryFile(
        delete=False,
        dir=TRANSCODING_DIR,
        suffix=suffix,
    ) as handle:
        if hasattr(uploaded_file, "chunks"):
            for chunk in uploaded_file.chunks():
                handle.write(chunk)
        else:
            handle.write(uploaded_file.read())
        return Path(handle.name)


def _record_media_upload(
    *,
    transfer_job: TransferJob,
    media_role: str,
    stored_name: str,
    content_hash: str,
    uploaded_name: str,
) -> None:
    provenance = _transfer_provenance(transfer_job.provenance)
    uploads = list(provenance.get("media_uploads") or [])
    uploads.append(
        {
            "media_role": media_role,
            "stored_name": stored_name,
            "content_hash": content_hash,
            "uploaded_name": uploaded_name,
        }
    )
    _update_transfer_provenance(transfer_job, media_uploads=uploads)


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
    resource_rows = transfer_job.resource_rows or {}
    video_payload = resource_rows.get("video_file") or {}
    if not isinstance(video_payload, dict):
        return str(video.processed_video_hash or "").strip()
    payload_hash = str(video_payload.get("processed_video_hash", "")).strip()
    if payload_hash:
        return payload_hash
    return str(video.processed_video_hash or "").strip()


def _mark_video_transfer_as_processed(video: VideoFile) -> None:
    state = video.get_or_create_state()
    state.mark_processing_started()
    state.mark_anonymized()
    state.mark_sensitive_meta_processed()
    state.mark_anonymization_validated()
    ProcessingHistory.mark_success(file_hash=video.video_hash, obj=video)


def _mark_report_transfer_as_processed(report: RawPdfFile) -> None:
    state = report.get_or_create_state()
    state.mark_processing_started()
    state.mark_anonymized()
    state.mark_sensitive_meta_processed()
    state.mark_anonymization_validated()
    ProcessingHistory.mark_success(file_hash=report.pdf_hash, obj=report)


def _sender_processing_success(transfer_job: TransferJob) -> bool | None:
    processing_snapshot = transfer_job.processing_snapshot or {}
    sender_processing_success = _coerce_optional_bool(
        processing_snapshot.get("sender_processing_success")
    )
    if sender_processing_success is not None:
        return sender_processing_success

    resource_rows = transfer_job.resource_rows or {}
    processing_history_payload = resource_rows.get("processing_history") or {}
    if isinstance(processing_history_payload, dict):
        return _coerce_optional_bool(processing_history_payload.get("success"))
    return None


def _normalized_suffix(upload_name: str, default_suffix: str) -> str:
    suffix = Path(upload_name).suffix.strip() or default_suffix
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return suffix.lower()


def _apply_video_file_payload(video: VideoFile, payload: dict[str, Any]) -> None:
    sync_fields = [
        "processed_video_hash",
        "original_file_name",
        "fps",
        "duration",
        "frame_count",
        "width",
        "height",
        "suffix",
        "meta",
    ]
    for field_name in sync_fields:
        if field_name in payload:
            setattr(video, field_name, payload[field_name])


def _apply_video_state_payload(
    video_state: VideoState, payload: dict[str, Any]
) -> None:
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
        "processing_started",
        "segment_annotations_created",
        "segment_annotations_validated",
        "was_created",
    ]
    updated_fields: list[str] = []
    for field_name in sync_fields:
        if field_name in payload:
            setattr(video_state, field_name, payload[field_name])
            updated_fields.append(field_name)
    if updated_fields:
        updated_fields.append("date_modified")
        video_state.save(update_fields=updated_fields)


def _apply_report_file_payload(report: RawPdfFile, payload: dict[str, Any]) -> None:
    sync_fields = [
        "text",
        "anonymized_text",
        "raw_meta",
        "state_report_processing_required",
        "state_report_processed",
    ]
    for field_name in sync_fields:
        if field_name in payload:
            setattr(report, field_name, payload[field_name])


def _apply_report_state_payload(
    report_state: RawPdfState, payload: dict[str, Any]
) -> None:
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
    ]
    updated_fields: list[str] = []
    for field_name in sync_fields:
        if field_name in payload:
            setattr(report_state, field_name, payload[field_name])
            updated_fields.append(field_name)
    if updated_fields:
        updated_fields.append("date_modified")
        report_state.save(update_fields=updated_fields)


def _upsert_sensitive_meta(
    *,
    existing: SensitiveMeta | None,
    payload: dict[str, Any],
    center: Center,
) -> SensitiveMeta:
    safe_fields = [
        "examination_date",
        "examination_time",
        "casenumber",
        "file_path",
        "patient_first_name",
        "patient_last_name",
        "patient_dob",
        "endoscope_type",
        "endoscope_sn",
        "text",
        "anonymized_text",
    ]
    payload_patient_hash = (
        str(payload.get("patient_hash", "")).strip()
        if payload.get("patient_hash")
        else ""
    )
    payload_examination_hash = (
        str(payload.get("examination_hash", "")).strip()
        if payload.get("examination_hash")
        else ""
    )
    sensitive_meta = existing or SensitiveMeta(center=center)
    sensitive_meta.center = center
    for field_name in safe_fields:
        if field_name in payload:
            normalized_value = _normalize_sensitive_meta_value(
                field_name, payload[field_name]
            )
            if normalized_value is None:
                continue
            setattr(sensitive_meta, field_name, normalized_value)
    sensitive_meta.save()
    if payload_patient_hash or payload_examination_hash:
        update_fields: list[str] = []
        if payload_patient_hash:
            sensitive_meta.patient_hash = payload_patient_hash
            update_fields.append("patient_hash")
        if payload_examination_hash:
            sensitive_meta.examination_hash = payload_examination_hash
            update_fields.append("examination_hash")
        if update_fields:
            SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
                **{field: getattr(sensitive_meta, field) for field in update_fields}
            )
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
        resolution.patient_examination.pk if resolution.patient_examination else None
    )
    transfer_job.linked_patient_id = (
        resolution.patient_examination.patient_id
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
    processing_snapshot: dict[str, Any],
) -> tuple[str, str, str]:
    local_history_success = ProcessingHistory.has_history_for_hash(
        file_hash=transfer_job.resource_hash,
        success=True,
    )
    local_raw_present = video.get_raw_file_path() is not None
    local_processed_present = video.get_processed_file_path() is not None
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

    if transfer_job.transfer_mode == TransferJob.TransferMode.METADATA_ONLY:
        if (
            transfer_job.processing_policy
            == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE
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
    local_raw_present = report.get_raw_file_path() is not None
    local_processed_present = report.anonymized_file_path is not None

    if local_history_success and (local_raw_present or local_processed_present):
        return (
            TransferJob.ProcessingDecision.SKIP_EXISTING_SUCCESS,
            TransferJob.TransferStatus.APPLIED,
            "Existing local processing history and artifacts allow replay suppression",
        )

    if transfer_job.transfer_mode == TransferJob.TransferMode.METADATA_ONLY:
        if (
            transfer_job.processing_policy
            == TransferJob.ProcessingPolicy.PRESERVE_PROCESSING_STATE
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
