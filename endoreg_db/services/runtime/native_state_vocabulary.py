"""Fail-closed parity between native Rust state vocabularies and their Python mirrors.

Rust declares every state, event and action token once. Python keeps one typed
mirror per vocabulary for static checking; this module is the only registry of
those mirrors and refuses startup when any mirror drifts in either direction.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from django.core.exceptions import ImproperlyConfigured

from endoreg_db.utils.rust_backend import native_state_vocabularies


def _enum_tokens(mirror: type[Enum]) -> frozenset[str]:
    # Enum members hash by name, so compare their wire values.
    return frozenset(str(member.value) for member in mirror)


def native_vocabulary_mirrors() -> dict[str, frozenset[str]]:
    """Python mirror tokens keyed by native vocabulary name."""
    from endoreg_db.models.hub.outbound_hub_transfer_job import OutboundHubTransferJob
    from endoreg_db.models.hub.transfer_job import TransferJob
    from endoreg_db.models.state.hub_transfer import HubExportFailureClass
    from endoreg_db.models.state.anonymization import (
        AnonymizationPhase,
        AnonymizationState,
    )
    from endoreg_db.models.state.frame_annotation import (
        FrameAnnotationStatus,
        FrameSamplingStrategy,
        FrameTaskMode,
    )
    from endoreg_db.models.state.video_segment_validation import (
        SegmentAnnotationStatus,
    )
    from endoreg_db.schemas.import_processing import (
        ImportMediaKind,
        ImportProcessingEvent,
        ImportProcessingStage,
    )
    from endoreg_db.services.runtime.lifecycle_state_machine import (
        OperationLifecycleEvent,
        OperationLifecycleState,
        ServiceLifecycleEvent,
        ServiceLifecycleState,
    )
    from endoreg_db.services.streaming.hls_media import HLS_IN_FLIGHT_STATUSES
    from endoreg_db.utils.rust_backend import (
        HlsPublicationAction,
        HlsReconciliationAction,
        HlsReservationAction,
        HubTransferEvent,
    )

    return {
        "hub_local_status": _enum_tokens(OutboundHubTransferJob.LocalStatus),
        "hub_remote_status": _enum_tokens(TransferJob.TransferStatus),
        "hub_failure_class": _enum_tokens(HubExportFailureClass),
        "hub_transfer_event": _enum_tokens(HubTransferEvent),
        "import_processing_stage": _enum_tokens(ImportProcessingStage),
        "import_media_kind": _enum_tokens(ImportMediaKind),
        "import_processing_event": _enum_tokens(ImportProcessingEvent),
        "anonymization_status": _enum_tokens(AnonymizationState),
        "anonymization_phase": _enum_tokens(AnonymizationPhase),
        "service_lifecycle_state": _enum_tokens(ServiceLifecycleState),
        "service_lifecycle_event": _enum_tokens(ServiceLifecycleEvent),
        "operation_lifecycle_state": _enum_tokens(OperationLifecycleState),
        "operation_lifecycle_event": _enum_tokens(OperationLifecycleEvent),
        "hls_in_flight_status": frozenset(HLS_IN_FLIGHT_STATUSES),
        "hls_reservation_action": _enum_tokens(HlsReservationAction),
        "hls_publication_action": _enum_tokens(HlsPublicationAction),
        "hls_reconciliation_action": _enum_tokens(HlsReconciliationAction),
        "segment_annotation_status": _enum_tokens(SegmentAnnotationStatus),
        "frame_annotation_status": _enum_tokens(FrameAnnotationStatus),
        "frame_task_mode": _enum_tokens(FrameTaskMode),
        "frame_sampling_strategy": _enum_tokens(FrameSamplingStrategy),
    }


def native_vocabulary_mismatches(
    native: Mapping[str, frozenset[str]],
    mirrors: Mapping[str, frozenset[str]],
) -> list[str]:
    """Describe every difference between the native vocabularies and the mirrors."""
    mismatches = [
        f"{name}: native vocabulary has no Python mirror"
        for name in sorted(native.keys() - mirrors.keys())
    ]
    mismatches.extend(
        f"{name}: Python mirror has no native vocabulary"
        for name in sorted(mirrors.keys() - native.keys())
    )
    for name in sorted(native.keys() & mirrors.keys()):
        missing_in_python = sorted(native[name] - mirrors[name])
        missing_in_rust = sorted(mirrors[name] - native[name])
        if missing_in_python:
            mismatches.append(f"{name}: missing in Python {missing_in_python}")
        if missing_in_rust:
            mismatches.append(f"{name}: missing in Rust {missing_in_rust}")
    return mismatches


def verify_native_state_vocabularies() -> None:
    """Refuse to run when a Python mirror differs from its Rust vocabulary."""
    mismatches = native_vocabulary_mismatches(
        native_state_vocabularies(), native_vocabulary_mirrors()
    )
    if mismatches:
        raise ImproperlyConfigured(
            "Native state vocabulary mismatch: " + "; ".join(mismatches)
        )
