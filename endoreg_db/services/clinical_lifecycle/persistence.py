"""Stored clinical workflow state: compare-and-swap writes, idempotency, audit.

The pure reducer stays in :mod:`.state_machine`; this module only persists its
results (media_workflow_state_machine/persistence_and_api). A record without a
stored row is reported as projected from its persisted facts; the first
accepted event seeds the row from that projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from django.db import IntegrityError, models, transaction
from django.db.models import F
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.models.state.clinical_workflow import (
    ClinicalWorkflowRecord,
    ClinicalWorkflowTransition,
)

from .facts import report_record_facts, video_record_facts
from .state_machine import (
    AnnotationStage,
    ClinicalLifecycleEvent,
    ClinicalLifecycleTransitionError,
    ClinicalRecordFacts,
    ClinicalRecordKind,
    ClinicalRecordState,
    PrivacyStage,
    ReleaseStage,
    derive_clinical_record_state,
    evidence_rejection,
    transition_clinical_record,
)

MAX_IDEMPOTENCY_KEY_LENGTH = 128
AUDIT_ACTION = "clinical_workflow_transition"


class WorkflowStateSource(StrEnum):
    STORED = "stored"
    PROJECTED = "projected"


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    kind: ClinicalRecordKind
    record_id: int
    state: ClinicalRecordState
    version: int
    source: WorkflowStateSource


@dataclass(frozen=True, slots=True)
class WorkflowTransitionResult:
    snapshot: WorkflowSnapshot
    replayed: bool


class WorkflowConcurrencyError(RuntimeError):
    """Another writer changed the record first; re-read before retrying."""


class IdempotencyKeyReuseError(ValueError):
    """The key was already used for a different event on this record."""


def record_facts(kind: ClinicalRecordKind, record_id: int) -> ClinicalRecordFacts:
    match kind:
        case ClinicalRecordKind.VIDEO:
            video = VideoFile.objects.select_related("state", "sensitive_meta").get(
                pk=record_id
            )
            return video_record_facts(video)
        case ClinicalRecordKind.REPORT:
            report = RawPdfFile.objects.select_related("state", "sensitive_meta").get(
                pk=record_id
            )
            return report_record_facts(report)


def stored_state(row: ClinicalWorkflowRecord) -> ClinicalRecordState:
    # Unknown persisted tokens raise ValueError instead of being guessed.
    return ClinicalRecordState(
        PrivacyStage(row.privacy),
        AnnotationStage(row.annotation),
        ReleaseStage(row.release),
    )


def _transition_state(
    row: ClinicalWorkflowTransition,
) -> ClinicalRecordState:
    return ClinicalRecordState(
        PrivacyStage(row.to_privacy),
        AnnotationStage(row.to_annotation),
        ReleaseStage(row.to_release),
    )


def read_workflow_state(
    kind: ClinicalRecordKind, record_id: int, facts: ClinicalRecordFacts
) -> WorkflowSnapshot:
    """Stored state, or the projection of ``facts`` before the first event."""
    row = ClinicalWorkflowRecord.objects.filter(
        kind=kind.value, record_id=record_id
    ).first()
    if row is not None:
        return WorkflowSnapshot(
            kind, record_id, stored_state(row), row.version, WorkflowStateSource.STORED
        )
    state = derive_clinical_record_state(facts)
    return WorkflowSnapshot(kind, record_id, state, 0, WorkflowStateSource.PROJECTED)


def _replay(
    kind: ClinicalRecordKind,
    record_id: int,
    idempotency_key: str,
    event: ClinicalLifecycleEvent,
) -> WorkflowTransitionResult | None:
    previous = ClinicalWorkflowTransition.objects.filter(
        record__kind=kind.value,
        record__record_id=record_id,
        idempotency_key=idempotency_key,
    ).first()
    if previous is None:
        return None
    if previous.event != event.value:
        raise IdempotencyKeyReuseError(
            f"Idempotency key already used for {previous.event} on {kind.value} "
            f"{record_id}."
        )
    snapshot = WorkflowSnapshot(
        kind,
        record_id,
        _transition_state(previous),
        previous.from_version + 1,
        WorkflowStateSource.STORED,
    )
    return WorkflowTransitionResult(snapshot=snapshot, replayed=True)


def _seeded_row(
    kind: ClinicalRecordKind, record_id: int, facts: ClinicalRecordFacts
) -> ClinicalWorkflowRecord:
    projected = derive_clinical_record_state(facts)
    row, _created = ClinicalWorkflowRecord.objects.get_or_create(
        kind=kind.value,
        record_id=record_id,
        defaults={
            "privacy": projected.privacy.value,
            "annotation": projected.annotation.value,
            "release": projected.release.value,
        },
    )
    return row


def _audit_actor(actor: object | None) -> models.Model | None:
    if getattr(actor, "is_authenticated", False):
        return cast(models.Model, actor)
    return None


def _state_payload(state: ClinicalRecordState) -> JsonObject:
    return {
        "privacy": state.privacy.value,
        "annotation": state.annotation.value,
        "release": state.release.value,
    }


def _apply(
    kind: ClinicalRecordKind,
    record_id: int,
    event: ClinicalLifecycleEvent,
    idempotency_key: str,
    actor: models.Model | None,
) -> WorkflowTransitionResult:
    replay = _replay(kind, record_id, idempotency_key, event)
    if replay is not None:
        return replay
    facts = record_facts(kind, record_id)
    row = _seeded_row(kind, record_id, facts)
    current = stored_state(row)
    rejection = evidence_rejection(facts, event)
    if rejection is not None:
        raise ClinicalLifecycleTransitionError(
            state=current, event=event, reason=rejection
        )
    target = transition_clinical_record(current, event)
    updated = ClinicalWorkflowRecord.objects.filter(
        pk=row.pk, version=row.version
    ).update(
        privacy=target.privacy.value,
        annotation=target.annotation.value,
        release=target.release.value,
        version=F("version") + 1,
    )
    if updated != 1:
        raise WorkflowConcurrencyError(
            f"{kind.value} {record_id} changed concurrently; re-read and retry."
        )
    ClinicalWorkflowTransition.objects.create(
        record=row,
        idempotency_key=idempotency_key,
        event=event.value,
        from_version=row.version,
        from_privacy=current.privacy.value,
        from_annotation=current.annotation.value,
        from_release=current.release.value,
        to_privacy=target.privacy.value,
        to_annotation=target.annotation.value,
        to_release=target.release.value,
        actor=actor,
    )
    AuditLedger.objects.create(
        user=actor,
        object_type="ClinicalWorkflowRecord",
        object_pk=str(row.pk),
        action=AUDIT_ACTION,
        data={
            "kind": kind.value,
            "record_id": record_id,
            "event": event.value,
            "from_version": row.version,
            "from": _state_payload(current),
            "to": _state_payload(target),
        },
    )
    snapshot = WorkflowSnapshot(
        kind, record_id, target, row.version + 1, WorkflowStateSource.STORED
    )
    return WorkflowTransitionResult(snapshot=snapshot, replayed=False)


def apply_workflow_event(
    *,
    kind: ClinicalRecordKind,
    record_id: int,
    event: ClinicalLifecycleEvent,
    idempotency_key: str,
    actor: object | None = None,
) -> WorkflowTransitionResult:
    """Apply ``event`` once per key: reducer, CAS write, transition row, audit.

    A repeated key for the same event returns the recorded result with
    ``replayed=True``; a key reused for another event raises. Side effects of
    a transition belong to its caller, after this function's commit.
    """
    key = idempotency_key.strip()
    if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ValueError(
            f"Idempotency key must be 1-{MAX_IDEMPOTENCY_KEY_LENGTH} characters."
        )
    audit_actor = _audit_actor(actor)
    try:
        with transaction.atomic():
            return _apply(kind, record_id, event, key, audit_actor)
    except IntegrityError:
        # A concurrent request committed the same key or version first.
        replay = _replay(kind, record_id, key, event)
        if replay is not None:
            return replay
        raise WorkflowConcurrencyError(
            f"{kind.value} {record_id} changed concurrently; re-read and retry."
        ) from None
