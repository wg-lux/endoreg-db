"""Exact, non-network resolution of current processed-media placements."""

from __future__ import annotations

from dataclasses import dataclass
import string

from endoreg_db.models.hub.storage_placement import (
    StorageArtifactKind,
    StorageArtifactPlacement,
)
from endoreg_db.models.hub.storage_transfer import StorageTransferEvidence
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile

STORAGE_ARTIFACT_RESOLUTION_CONTRACT_VERSION = "hub-storage-artifact-resolution-v1"


class StorageArtifactResolutionError(ValueError):
    """The current processed generation has no unique verified placement."""


@dataclass(frozen=True, slots=True)
class CurrentStorageArtifact:
    contract_version: str
    placement_id: str
    artifact_key: str
    artifact_kind: StorageArtifactKind
    node_key: str
    ciphertext_sha256: str
    plaintext_sha256: str
    plaintext_size: int
    media_lease_video_id: int | None


def _digest(value: object) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(
        character not in string.hexdigits for character in digest
    ):
        raise StorageArtifactResolutionError(
            "current processed generation has no valid SHA-256 identity"
        )
    return digest


def _resolve(
    *,
    artifact_key: str,
    artifact_kind: StorageArtifactKind,
    plaintext_sha256: str,
    media_lease_video_id: int | None,
) -> CurrentStorageArtifact:
    placements = list(
        StorageArtifactPlacement.objects.select_related("storage_node__node")
        .filter(
            artifact_key=artifact_key,
            artifact_kind=artifact_kind,
            sha256=plaintext_sha256,
            media_lease_video_id=media_lease_video_id,
            state=StorageArtifactPlacement.State.COMMITTED,
            role=StorageArtifactPlacement.Role.PRIMARY,
        )
        .order_by("pk")[:2]
    )
    if len(placements) != 1:
        raise StorageArtifactResolutionError(
            "current processed generation has no unique committed primary placement"
        )
    placement = placements[0]
    evidence_rows = list(
        StorageTransferEvidence.objects.filter(
            placement=placement,
            state=StorageTransferEvidence.State.VERIFIED,
            node_key=placement.storage_node.node.node_key,
            plaintext_sha256=plaintext_sha256,
            plaintext_size=placement.expected_size_bytes,
        ).order_by("pk")[:2]
    )
    if len(evidence_rows) != 1:
        raise StorageArtifactResolutionError(
            "current processed generation has no unique verified transfer evidence"
        )
    evidence = evidence_rows[0]
    return CurrentStorageArtifact(
        contract_version=STORAGE_ARTIFACT_RESOLUTION_CONTRACT_VERSION,
        placement_id=str(placement.pk),
        artifact_key=artifact_key,
        artifact_kind=artifact_kind,
        node_key=evidence.node_key,
        ciphertext_sha256=evidence.ciphertext_sha256,
        plaintext_sha256=evidence.plaintext_sha256,
        plaintext_size=evidence.plaintext_size,
        media_lease_video_id=media_lease_video_id,
    )


def resolve_current_processed_video_storage(*, video_id: int) -> CurrentStorageArtifact:
    video = VideoFile.objects.select_related("state").get(pk=video_id)
    state = video.state
    if not state.ready_for_export:
        raise StorageArtifactResolutionError("video is not approved for export")
    digest = _digest(state.processed_file_sha256)
    if _digest(video.processed_video_hash) != digest:
        raise StorageArtifactResolutionError(
            "video generation hash does not match its export approval"
        )
    return _resolve(
        artifact_key=f"video:{video.pk}:processed:{digest}",
        artifact_kind=StorageArtifactKind.ANONYMIZED_VIDEO,
        plaintext_sha256=digest,
        media_lease_video_id=int(video.pk),
    )


def resolve_current_processed_report_storage(
    *, report_id: int
) -> CurrentStorageArtifact:
    report = RawPdfFile.objects.select_related("state").get(pk=report_id)
    state = report.state
    if state is None:
        raise StorageArtifactResolutionError("report has no processing state")
    if not state.anonymization_validated:
        raise StorageArtifactResolutionError("report is not human validated")
    digest = _digest(state.processed_file_sha256)
    return _resolve(
        artifact_key=f"report:{report.pk}:processed:{digest}",
        artifact_kind=StorageArtifactKind.PROCESSED_REPORT,
        plaintext_sha256=digest,
        media_lease_video_id=None,
    )


__all__ = [
    "STORAGE_ARTIFACT_RESOLUTION_CONTRACT_VERSION",
    "CurrentStorageArtifact",
    "StorageArtifactResolutionError",
    "resolve_current_processed_report_storage",
    "resolve_current_processed_video_storage",
]
