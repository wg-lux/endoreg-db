"""Explicit, audited adoption of existing HTTP Live Streaming (HLS) output.

This is an operator attestation of historical source equivalence, not an inferred
proof from a filename, duration, or color. Ordinary readiness remains unchanged.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
import socket
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services import hls_media
from endoreg_db.services.media_operation_gate import defer_if_video_media_busy
from endoreg_db.utils.file_operations import atomic_create_file
from endoreg_db.utils.paths import EndoregPathsModel, ensure_within_protected_root

logger = logging.getLogger(__name__)


class AdoptionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    receipt_id: UUID
    host: str
    approved_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approved_at: datetime
    artifact_id: int
    video_id: int
    artifact_kind: str
    key_id: UUID
    encoding_profile_name: str
    previous_source_file_name: str
    previous_source_generation_id: UUID
    previous_source_content_hash: Literal[""] = ""
    source_file_name: str
    source_generation_id: UUID
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cancelled_queued_attempts: tuple[int, ...]
    status: Literal["dry_run", "prepared", "committed"]


def _write_receipt(receipt: AdoptionReceipt) -> None:
    destination = ensure_within_protected_root(
        EndoregPathsModel.from_environment().protected_root
        / "hls-legacy-adoption"
        / f"{receipt.receipt_id}-{receipt.status}.json"
    )
    atomic_create_file(
        destination=destination,
        content=[receipt.model_dump_json(indent=2).encode("utf-8")],
        file_mode=0o600,
        dir_mode=0o700,
    )


def adopt_legacy_hls(
    *,
    artifact_id: int,
    approved_by: str,
    reason: str,
    created_before: datetime,
    accept_unchanged_source: bool,
    apply: bool = False,
) -> AdoptionReceipt:
    if not accept_unchanged_source or not approved_by.strip() or not reason.strip():
        raise ValueError(
            "Explicit unchanged-source attestation, approver and reason are required"
        )
    if not timezone.is_aware(created_before):
        raise ValueError("Legacy cutoff must include a timezone")
    # Always lock the video before its artifacts, matching publication/claim order.
    video_id = VideoHlsArtifact.objects.only("video_id").get(pk=artifact_id).video_id
    with transaction.atomic():
        video = VideoFile.objects.select_for_update().get(pk=video_id)
        artifact = VideoHlsArtifact.objects.select_for_update().get(
            pk=artifact_id, video=video
        )
        if (
            artifact.status != VideoHlsArtifact.Status.READY.value
            or artifact.source_content_hash
        ):
            raise ValueError(
                "Only existing READY artifacts with a blank historical hash may be adopted"
            )
        if artifact.created_at >= created_before:
            raise ValueError("Artifact is newer than the explicit legacy cutoff")
        defer_if_video_media_busy(video_id=video_id)
        active = list(
            VideoHlsArtifact.objects.select_for_update().filter(
                video=video,
                artifact_kind=artifact.artifact_kind,
                status__in=hls_media.HLS_IN_FLIGHT_STATUSES,
            )
        )
        if any(a.status != VideoHlsArtifact.Status.QUEUED.value for a in active):
            raise RuntimeError(
                "An active HLS encode or publication must finish before adoption"
            )
        kind = hls_media.coerce_hls_artifact_kind(artifact.artifact_kind)
        source = hls_media._hls_source(video, kind)
        timeline = hls_media._hls_timeline_validation(video, kind)
        hls_media.hls_encoding_profile_by_name(artifact.encoding_profile_name)
        if not hls_media._ready_artifact_paths_exist(artifact):
            raise ValueError("Legacy playlist or referenced segments are incomplete")
        # Authenticates the wrapped key without exposing it in output or receipts.
        hls_media.unwrap_hls_content_key(artifact)
        source_hash = hls_media._source_content_hash(source)
        if artifact.source_file_name != source.source_file_name:
            # A different filename needs exact authenticated content equivalence.
            old_source = FieldFile(
                video, source.field_file.field, artifact.source_file_name
            )
            if hls_media.verified_video_source_hash(old_source) != source_hash:
                raise ValueError(
                    "Previous and current source files contain different media"
                )
        for queued in active:
            if (
                queued.source_file_name != source.source_file_name
                or queued.source_content_hash != source_hash
                or queued.source_generation_id != timeline.source_generation_id
            ):
                raise ValueError(
                    "Queued replacement targets a different source identity"
                )
        receipt = AdoptionReceipt(
            receipt_id=uuid4(),
            host=socket.gethostname(),
            approved_by=approved_by,
            reason=reason,
            approved_at=timezone.now(),
            artifact_id=int(artifact.pk),
            video_id=video_id,
            artifact_kind=artifact.artifact_kind,
            key_id=artifact.key_id,
            encoding_profile_name=artifact.encoding_profile_name,
            previous_source_file_name=artifact.source_file_name,
            previous_source_generation_id=artifact.source_generation_id,
            source_file_name=source.source_file_name,
            source_generation_id=timeline.source_generation_id,
            source_content_hash=source_hash,
            cancelled_queued_attempts=tuple(int(a.pk) for a in active),
            status="prepared" if apply else "dry_run",
        )
        if not apply:
            return receipt
        # A durable before-image is required before any database mutation.
        _write_receipt(receipt)
        artifact.source_content_hash = source_hash
        artifact.source_generation_id = timeline.source_generation_id
        artifact.source_file_name = source.source_file_name
        artifact.save(
            update_fields=[
                "source_content_hash",
                "source_generation_id",
                "source_file_name",
                "updated_at",
            ]
        )
        for queued in active:
            queued.status = VideoHlsArtifact.Status.FAILED.value
            queued.error_code = VideoHlsArtifact.ErrorCode.STALE_ATTEMPT.value
            queued.last_error = f"Replacement cancelled by approved legacy HLS adoption {receipt.receipt_id}"
            queued.save(
                update_fields=["status", "error_code", "last_error", "updated_at"]
            )
        # Exercise both ordinary admission paths before committing the metadata repair.
        hls_media.get_ready_hls_artifact(video=video, artifact_kind=kind)
        hls_media.get_ready_hls_artifact_by_key(video=video, key_id=artifact.key_id)
    committed = receipt.model_copy(update={"status": "committed"})
    _write_receipt(committed)
    logger.info(
        "Legacy HLS adoption committed",
        extra={
            "event": "hls_legacy_adoption",
            "receipt_id": str(receipt.receipt_id),
            "artifact_id": artifact_id,
            "video_id": video_id,
        },
    )
    return committed
