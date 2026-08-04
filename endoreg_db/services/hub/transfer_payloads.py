from __future__ import annotations

from pathlib import Path
from typing import Any

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hub.transfer_logging import (
    decision,
    info,
    json_block,
    kv,
    path_info,
    section,
    step,
    success,
    warning,
)
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.security.hashs import get_pdf_hash


def _bool(obj: object, field: str) -> bool:
    """
    Safely resolve a Boolean-like model attribute.

    Missing attributes are treated as False so payload construction remains
    compatible with older state-model versions.
    """
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
    """
    Build the portable transfer payload for an anonymized processed video.

    The sender-local VideoFile primary key is retained only in provenance.
    It is deliberately not included in resource_rows.video_file because local
    database IDs are not portable between nodes.

    Returns:
        A tuple containing:
        - the JSON-compatible transfer payload;
        - the processed media path, or None in metadata-only mode;
        - the upload content type.
    """
    section("BUILD VIDEO TRANSFER PAYLOAD", "📦")

    kv("Source VideoFile local ID", video.pk)
    kv("Transfer key", transfer_key)
    kv("Source node key", source_node_key)
    kv("Target node key", target_node_key)
    kv("Source center key", source_center_key or "<not supplied>")
    kv("Metadata only", metadata_only)

    step(1, "Resolve processed video file")

    if not video.processed_file:
        raise ValueError(f"VideoFile {video.pk} has no processed_file")

    processed_path = Path(video.processed_file.path).resolve()

    path_info(
        label="Processed video path",
        path=processed_path,
        check_exists=True,
    )

    kv(
        "Processed storage name",
        getattr(video.processed_file, "name", None),
    )
    kv(
        "Original filename",
        video.original_file_name or Path(video.processed_file.name).name,
    )

    if not processed_path.is_file():
        raise FileNotFoundError(f"Processed video missing: {processed_path}")

    success("Processed video file exists and is readable from the local filesystem")

    step(2, "Resolve portable hashes")

    processed_hash = str(video.processed_video_hash or "").strip()

    if processed_hash:
        info("Using processed_video_hash stored in the source VideoFile database row")
    else:
        warning(
            "VideoFile.processed_video_hash is empty; calculating SHA-256 "
            "from the processed MP4"
        )
        processed_hash = sha256_file(processed_path)

    database_video_hash = str(video.video_hash or "").strip()

    if database_video_hash:
        info("Using video_hash stored in the source VideoFile database row")
        video_hash = database_video_hash
    else:
        warning(
            "VideoFile.video_hash is empty; using the processed-media hash "
            "as the portable video identity"
        )
        video_hash = processed_hash

    kv("Video hash", video_hash)
    kv("Processed video hash", processed_hash)
    kv("Hashes are identical", video_hash == processed_hash)

    step(3, "Load source VideoState")

    state = get_or_create_video_state(video)

    kv("VideoState local ID", state.pk)
    kv("Frames extracted", state.frames_extracted)
    kv("Frames initialized", state.frames_initialized)
    kv("State frame count", state.frame_count)
    kv("Video metadata extracted", state.video_meta_extracted)
    kv("Text metadata extracted", state.text_meta_extracted)
    kv("Sensitive metadata processed", state.sensitive_meta_processed)
    kv("Anonymized", state.anonymized)
    kv("Anonymization validated", state.anonymization_validated)
    kv("Processing started", state.processing_started)
    kv("Processing error", state.processing_error)
    kv("Resolved status", state.anonymization_status.value)

    step(4, "Read transferable VideoFile metadata")

    original_file_name = (
        video.original_file_name or Path(video.processed_file.name).name
    )
    suffix = getattr(video, "suffix", "") or ".mp4"
    fps = getattr(video, "fps", None)
    duration = getattr(video, "duration", None)
    frame_count = getattr(video, "frame_count", None)
    width = getattr(video, "width", None)
    height = getattr(video, "height", None)
    meta = getattr(video, "meta", None)

    kv("Original file name", original_file_name)
    kv("Suffix", suffix)
    kv("FPS", fps)
    kv("Duration", duration)
    kv("VideoFile frame count", frame_count)
    kv("Width", width)
    kv("Height", height)
    kv("Meta present", meta is not None)

    step(5, "Construct portable video payload")

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
                # Sender-local database IDs must not be included here.
                # Portable identity is provided by video_hash and
                # processed_video_hash.
                "video_hash": video_hash,
                "processed_video_hash": processed_hash,
                "original_file_name": original_file_name,
                "suffix": suffix,
                "fps": fps,
                "duration": duration,
                "frame_count": frame_count,
                "width": width,
                "height": height,
                "meta": meta,
            },
            "video_state": {
                "frames_extracted": _bool(state, "frames_extracted"),
                "frames_initialized": _bool(state, "frames_initialized"),
                "frame_count": getattr(state, "frame_count", None),
                "video_meta_extracted": _bool(
                    state,
                    "video_meta_extracted",
                ),
                "text_meta_extracted": _bool(
                    state,
                    "text_meta_extracted",
                ),
                "sensitive_meta_processed": _bool(
                    state,
                    "sensitive_meta_processed",
                ),
                "anonymized": _bool(state, "anonymized"),
                "anonymization_validated": _bool(
                    state,
                    "anonymization_validated",
                ),
                "processing_started": _bool(
                    state,
                    "processing_started",
                ),
                "processing_error": _bool(
                    state,
                    "processing_error",
                ),
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
            # This is audit information only. It must never be used as the
            # receiver-side VideoFile primary key.
            "source_video_id": video.pk,
        },
    }

    if source_center_key:
        payload["source_center_key"] = source_center_key

    step(6, "Payload construction complete")

    json_block(
        "Video resource rows",
        payload["resource_rows"],
    )
    json_block(
        "Processing snapshot",
        payload["processing_snapshot"],
    )
    json_block(
        "Transfer provenance",
        payload["provenance"],
    )

    decision("SENDER MEDIA DECISION")

    if metadata_only:
        warning("Only JSON metadata will be transmitted")
        kv("Returned media path", None)
    else:
        success("Processed media will be uploaded after receiver metadata creation")
        path_info(
            label="Returned media path",
            path=processed_path,
            check_exists=True,
        )
        kv("Upload content type", "video/mp4")

    return (
        payload,
        None if metadata_only else processed_path,
        "video/mp4",
    )


def build_report_transfer_payload(
    *,
    report: RawPdfFile,
    transfer_key: str,
    source_node_key: str,
    target_node_key: str,
    source_center_key: str | None,
    metadata_only: bool,
) -> tuple[dict[str, Any], Path | None, str | None]:
    """
    Build the portable transfer payload for an anonymized processed report.

    The sender-local RawPdfFile primary key is retained only in provenance.
    It is not included in resource_rows.raw_pdf_file because local database
    primary keys are not portable between nodes.
    """
    section("BUILD REPORT TRANSFER PAYLOAD", "📄")

    kv("Source RawPdfFile local ID", report.pk)
    kv("Transfer key", transfer_key)
    kv("Source node key", source_node_key)
    kv("Target node key", target_node_key)
    kv("Source center key", source_center_key or "<not supplied>")
    kv("Metadata only", metadata_only)

    step(1, "Resolve processed report file")

    processed_field = getattr(report, "processed_file", None) or getattr(
        report,
        "file",
        None,
    )

    if not processed_field:
        raise ValueError(f"RawPdfFile {report.pk} has no processed/file field")

    processed_path = Path(processed_field.path).resolve()

    path_info(
        label="Processed report path",
        path=processed_path,
        check_exists=True,
    )
    kv(
        "Processed storage name",
        getattr(processed_field, "name", None),
    )

    if not processed_path.is_file():
        raise FileNotFoundError(f"Processed report missing: {processed_path}")

    success("Processed report file exists and is readable")

    step(2, "Resolve portable report hash")

    pdf_hash = str(getattr(report, "pdf_hash", "") or "").strip()

    if pdf_hash:
        info("Using pdf_hash stored in the RawPdfFile database row")
    else:
        warning(
            "RawPdfFile.pdf_hash is empty; calculating the hash from "
            "the processed report"
        )
        pdf_hash = get_pdf_hash(processed_path)

    kv("PDF hash", pdf_hash)

    step(3, "Load source RawPdfState")

    state = get_or_create_raw_pdf_state(report)

    kv("RawPdfState local ID", state.pk)
    kv("Text metadata extracted", state.text_meta_extracted)
    kv(
        "Initial prediction completed",
        state.initial_prediction_completed,
    )
    kv(
        "Sensitive metadata processed",
        state.sensitive_meta_processed,
    )
    kv("Anonymized", state.anonymized)
    kv(
        "Anonymization validated",
        state.anonymization_validated,
    )
    kv("Processing started", state.processing_started)
    kv("Processing error", state.processing_error)
    kv("PDF metadata extracted", state.pdf_meta_extracted)
    kv("Resolved status", state.anonymization_status.value)

    step(4, "Construct portable report payload")

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
                # Sender-local database IDs must not be included here.
                "pdf_hash": pdf_hash,
                "text": getattr(report, "text", "") or "",
                "anonymized_text": (getattr(report, "anonymized_text", "") or ""),
                "raw_meta": getattr(report, "raw_meta", None),
                "state_report_processing_required": getattr(
                    report,
                    "state_report_processing_required",
                    None,
                ),
                "state_report_processed": getattr(
                    report,
                    "state_report_processed",
                    None,
                ),
            },
            "raw_pdf_state": {
                "text_meta_extracted": _bool(
                    state,
                    "text_meta_extracted",
                ),
                "initial_prediction_completed": _bool(
                    state,
                    "initial_prediction_completed",
                ),
                "sensitive_meta_processed": _bool(
                    state,
                    "sensitive_meta_processed",
                ),
                "anonymized": _bool(state, "anonymized"),
                "anonymization_validated": _bool(
                    state,
                    "anonymization_validated",
                ),
                "processing_started": _bool(
                    state,
                    "processing_started",
                ),
                "processing_error": _bool(
                    state,
                    "processing_error",
                ),
                "pdf_meta_extracted": _bool(
                    state,
                    "pdf_meta_extracted",
                ),
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
            # Audit-only sender-side identifier.
            "source_report_id": report.pk,
        },
    }

    if source_center_key:
        payload["source_center_key"] = source_center_key

    step(5, "Payload construction complete")

    json_block(
        "Report resource rows",
        payload["resource_rows"],
    )
    json_block(
        "Processing snapshot",
        payload["processing_snapshot"],
    )
    json_block(
        "Transfer provenance",
        payload["provenance"],
    )

    decision("SENDER REPORT MEDIA DECISION")

    if metadata_only:
        warning("Only report JSON metadata will be transmitted")
        kv("Returned media path", None)
    else:
        success("Processed report media will be uploaded after metadata creation")
        path_info(
            label="Returned media path",
            path=processed_path,
            check_exists=True,
        )
        kv("Upload content type", "application/pdf")

    return (
        payload,
        None if metadata_only else processed_path,
        "application/pdf",
    )
