"""Atomic, idempotent activation import and authenticated source consumption."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from cryptography.exceptions import InvalidTag

from django.core.files.base import ContentFile
from django.db import transaction
from lx_dtypes.models.contracts.electrosurgical_activations import (
    MAX_ACTIVATION_CSV_BYTES,
)
from lx_dtypes.models.contracts.video_provenance import RecordingSynchronization

from endoreg_db.models.media.video.electrosurgical_activation import (
    VideoElectrosurgicalActivationLog,
    activation_export_upload_to,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.electrosurgical_activation import (
    ActivationImportMetadata,
    ActivationSourceFormat,
)
from endoreg_db.services.electrical.activation_import import (
    ParsedActivationExport,
    parse_activation_export,
)
from endoreg_db.services.hub.video_provenance import record_video_synchronization
from endoreg_db.utils.file_operations import safe_delete_field_file
from endoreg_db.utils.hashs import get_bytes_hash
from endoreg_db.utils.storage_streaming import field_file_size, iter_field_file_bytes
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActivationImportResult:
    activation_log: VideoElectrosurgicalActivationLog
    created: bool


def import_activation_export(
    video_id: int,
    *,
    content: bytes,
    source_format: ActivationSourceFormat,
    source_timezone: str,
    synchronization: RecordingSynchronization,
    max_timing_error_seconds: Decimal,
) -> ActivationImportResult:
    parsed = parse_activation_export(
        content, source_format=source_format, source_timezone=source_timezone
    )
    metadata = ActivationImportMetadata(
        source_sha256=parsed.source_sha256,
        normalized_sha256=get_bytes_hash(parsed.log.model_dump_json().encode()),
        source_format=source_format,
        source_timezone=source_timezone,
        activation_count=len(parsed.log.activations),
        synchronization=synchronization,
        timestamp_rounding_error_seconds=parsed.timestamp_rounding_error_seconds,
        max_timing_error_seconds=max_timing_error_seconds,
    )
    with transaction.atomic():
        video = VideoFile.objects.select_for_update().get(pk=video_id)
        record_video_synchronization(video, synchronization)
        existing = VideoElectrosurgicalActivationLog.objects.filter(
            video=video, source_sha256=parsed.source_sha256
        ).first()
        if existing is not None:
            if ActivationImportMetadata.model_validate(existing.metadata) != metadata:
                raise ValueError(
                    "Activation source already exists with a different binding or profile"
                )
            load_activation_export(existing)
            return ActivationImportResult(existing, False)
        record = VideoElectrosurgicalActivationLog(
            video=video,
            source_sha256=parsed.source_sha256,
            metadata=metadata.model_dump(mode="json"),
        )
        record.clean()
        record.source_file.name = record.source_file.storage.save(
            activation_export_upload_to(record, ""), ContentFile(content)
        )
        try:
            with transaction.atomic():
                record.save()
        except Exception:
            safe_delete_field_file(record.source_file, missing_ok=True)
            raise
    emit_structured_event(
        logger,
        "electrosurgical_activation_import",
        status="imported",
        video_id=video_id,
        activation_log_id=record.pk,
        source_sha256=parsed.source_sha256,
        activation_count=metadata.activation_count,
    )
    return ActivationImportResult(record, True)


def load_activation_export(
    record: VideoElectrosurgicalActivationLog,
) -> ParsedActivationExport:
    try:
        return _load_activation_export(record)
    except (ValueError, OSError, RuntimeError, InvalidTag) as exc:
        emit_structured_event(
            logger,
            "electrosurgical_activation_integrity",
            level=logging.ERROR,
            status="failed",
            activation_log_id=record.pk,
            error_type=type(exc).__name__,
        )
        raise ValueError("Activation source failed verification") from exc


def _load_activation_export(
    record: VideoElectrosurgicalActivationLog,
) -> ParsedActivationExport:
    metadata = ActivationImportMetadata.model_validate(record.metadata)
    size = field_file_size(record.source_file)
    if not 0 < size <= MAX_ACTIVATION_CSV_BYTES:
        raise ValueError("Activation source size is invalid")
    content = b"".join(iter_field_file_bytes(record.source_file, start=0, end=size - 1))
    if (
        get_bytes_hash(content) != record.source_sha256
        or metadata.source_sha256 != record.source_sha256
    ):
        raise ValueError("Activation source failed its integrity check")
    parsed = parse_activation_export(
        content,
        source_format=metadata.source_format,
        source_timezone=metadata.source_timezone,
    )
    if (
        get_bytes_hash(parsed.log.model_dump_json().encode())
        != metadata.normalized_sha256
        or len(parsed.log.activations) != metadata.activation_count
        or parsed.timestamp_rounding_error_seconds
        != metadata.timestamp_rounding_error_seconds
    ):
        raise ValueError("Activation normalization differs from the imported identity")
    return parsed
