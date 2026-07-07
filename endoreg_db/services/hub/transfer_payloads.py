from __future__ import annotations

from pathlib import Path
from typing import Any

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.security.hashs import get_pdf_hash


def _bool(obj: object, field: str) -> bool:
    return bool(getattr(obj, field, False))


def build_video_transfer_payload(
    *,
    video: VideoFile,
    transfer_key: str,
    source_node_key: str,
    target_node_key: str,
    source_center_key: str | None,
    metadata_only: bool,
) -> tuple[dict[str, Any], Path | None, str | None]:
    if not video.processed_file:
        raise ValueError(f"VideoFile {video.pk} has no processed_file")

    processed_path = Path(video.processed_file.path).resolve()
    if not processed_path.is_file():
        raise FileNotFoundError(f"Processed video missing: {processed_path}")

    processed_hash = str(video.processed_video_hash or "").strip()
    if not processed_hash:
        processed_hash = sha256_file(processed_path)

    video_hash = str(video.video_hash or "").strip() or processed_hash
    state = get_or_create_video_state(video)

    payload: dict[str, Any] = {
        "transfer_key": transfer_key,
        "source_node_key": source_node_key,
        "target_node_key": target_node_key,
        "resource_kind": "video",
        "resource_hash": video_hash,
        "transfer_mode": (
            "metadata_only" if metadata_only else "metadata_and_processed_media"
        ),
        "processing_policy": "preserve_processing_state",
        "processing_intent": "sender_requests_state_preservation",
        "cleanup_policy": "retain_all",
        "payload_schema_version": "1.0",
        "resource_rows": {
            "video_file": {
                "id": video.pk,
                "video_hash": video_hash,
                "processed_video_hash": processed_hash,
                "original_file_name": video.original_file_name
                or Path(video.processed_file.name).name,
                "suffix": getattr(video, "suffix", "") or ".mp4",
                "fps": getattr(video, "fps", None),
                "duration": getattr(video, "duration", None),
                "frame_count": getattr(video, "frame_count", None),
                "width": getattr(video, "width", None),
                "height": getattr(video, "height", None),
                "meta": getattr(video, "meta", None),
            },
            "video_state": {
                "frames_extracted": _bool(state, "frames_extracted"),
                "frames_initialized": _bool(state, "frames_initialized"),
                "frame_count": getattr(state, "frame_count", None),
                "video_meta_extracted": _bool(state, "video_meta_extracted"),
                "text_meta_extracted": _bool(state, "text_meta_extracted"),
                "sensitive_meta_processed": _bool(state, "sensitive_meta_processed"),
                "anonymized": _bool(state, "anonymized"),
                "anonymization_validated": _bool(state, "anonymization_validated"),
                "processing_started": _bool(state, "processing_started"),
                "processing_error": _bool(state, "processing_error"),
                "was_created": True,
            },
            "processing_history": {
                "success": True,
            },
        },
        "processing_snapshot": {
            "sender_processing_success": True,
        },
        "provenance": {
            "source_node_key": source_node_key,
            "target_node_key": target_node_key,
            "source_video_id": video.pk,
        },
    }

    if source_center_key:
        payload["source_center_key"] = source_center_key

    return payload, (None if metadata_only else processed_path), "video/mp4"


def build_report_transfer_payload(
    *,
    report: RawPdfFile,
    transfer_key: str,
    source_node_key: str,
    target_node_key: str,
    source_center_key: str | None,
    metadata_only: bool,
) -> tuple[dict[str, Any], Path | None, str | None]:
    processed_field = getattr(report, "processed_file", None) or getattr(
        report, "file", None
    )
    if not processed_field:
        raise ValueError(f"RawPdfFile {report.pk} has no processed/file field")

    processed_path = Path(processed_field.path).resolve()
    if not processed_path.is_file():
        raise FileNotFoundError(f"Processed report missing: {processed_path}")

    pdf_hash = str(getattr(report, "pdf_hash", "") or "").strip()
    if not pdf_hash:
        pdf_hash = get_pdf_hash(processed_path)

    state = get_or_create_raw_pdf_state(report)

    payload: dict[str, Any] = {
        "transfer_key": transfer_key,
        "source_node_key": source_node_key,
        "target_node_key": target_node_key,
        "resource_kind": "report",
        "resource_hash": pdf_hash,
        "transfer_mode": (
            "metadata_only" if metadata_only else "metadata_and_processed_media"
        ),
        "processing_policy": "preserve_processing_state",
        "processing_intent": "sender_requests_state_preservation",
        "cleanup_policy": "retain_all",
        "payload_schema_version": "1.0",
        "resource_rows": {
            "raw_pdf_file": {
                "id": report.pk,
                "pdf_hash": pdf_hash,
                "text": getattr(report, "text", "") or "",
                "anonymized_text": getattr(report, "anonymized_text", "") or "",
                "raw_meta": getattr(report, "raw_meta", None),
                "state_report_processing_required": getattr(
                    report, "state_report_processing_required", None
                ),
                "state_report_processed": getattr(
                    report, "state_report_processed", None
                ),
            },
            "raw_pdf_state": {
                "text_meta_extracted": _bool(state, "text_meta_extracted"),
                "initial_prediction_completed": _bool(
                    state, "initial_prediction_completed"
                ),
                "sensitive_meta_processed": _bool(state, "sensitive_meta_processed"),
                "anonymized": _bool(state, "anonymized"),
                "anonymization_validated": _bool(state, "anonymization_validated"),
                "processing_started": _bool(state, "processing_started"),
                "processing_error": _bool(state, "processing_error"),
                "pdf_meta_extracted": _bool(state, "pdf_meta_extracted"),
                "was_created": True,
            },
            "processing_history": {
                "success": True,
            },
        },
        "processing_snapshot": {
            "sender_processing_success": True,
        },
        "provenance": {
            "source_node_key": source_node_key,
            "target_node_key": target_node_key,
            "source_report_id": report.pk,
        },
    }

    if source_center_key:
        payload["source_center_key"] = source_center_key

    return payload, (None if metadata_only else processed_path), "application/pdf"