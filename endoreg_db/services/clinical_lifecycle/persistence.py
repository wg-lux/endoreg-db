"""Stored clinical workflow state: compare-and-swap writes, idempotency, audit.

The pure reducer stays in :mod:`.state_machine`; this module only persists its
results (media_workflow_state_machine/persistence_and_api).

Single source of truth: the persisted media facts project the state. A stored
row is the versioned event journal of that state and must agree with the
projection on every track; only the privacy stages the projection cannot
express (quarantined, withdrawn) are carried by the row alone. Every reader
goes through :func:`resolve_workflow_state`, which fails closed with
:class:`WorkflowStateDriftError` on disagreement. A caller of
:func:`apply_workflow_event` therefore commits the fact change that realizes the
event in the same transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from django.db import IntegrityError, models, transaction
from django.db.models import F
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.media_workflow_api import StateSource

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.models.state.clinical_workflow import (
    ClinicalWorkflowRecord,
    ClinicalWorkflowTransition,
)

from endoreg_db.utils.structured_logging import emit_structured_event

from .facts import report_record_facts, video_record_facts
from .state_machine import (
    AnnotationStage,
    ClinicalLifecycleEvent,
    ClinicalRecordFacts,
    ClinicalRecordKind,
    ClinicalRecordState,
    PrivacyStage,
    ReleaseStage,
    derive_clinical_record_state,
    guarded_clinical_transition,
)

logger = logging.getLogger(__name__)

MAX_IDEMPOTENCY_KEY_LENGTH = 128
# Privacy stages without persisted fact evidence; only stored events set them.
UNPROJECTABLE_PRIVACY = frozenset({PrivacyStage.QUARANTINED, PrivacyStage.WITHDRAWN})
AUDIT_ACTION = "clinical_workflow_transition"


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    kind: ClinicalRecordKind
    record_id: int
    state: ClinicalRecordState
    version: int
    source: StateSource


@dataclass(frozen=True, slots=True)
class WorkflowTransitionResult:
    snapshot: WorkflowSnapshot
    replayed: bool


class WorkflowConcurrencyError(RuntimeError):
    """Another writer changed the record first; re-read before retrying."""


class IdempotencyKeyReuseError(ValueError):
    """The key was already used for a different event on this record."""


class WorkflowStateDriftError(RuntimeError):
    """A stored workflow row disagrees with the projection of persisted facts."""

    def __init__(
        self,
        *,
        kind: ClinicalRecordKind,
        record_id: int,
        stored: ClinicalRecordState,
        projected: ClinicalRecordState,
    ) -> None:
        self.kind = kind
        self.record_id = record_id
        self.stored = stored
        self.projected = projected
        super().__init__(
            f"{kind.value} {record_id}: stored workflow state "
            f"{_state_label(stored)} disagrees with persisted facts "
            f"{_state_label(projected)}"
        )


def _state_label(state: ClinicalRecordState) -> str:
    return f"{state.privacy.value}/{state.annotation.value}/{state.release.value}"


def _agrees(stored: ClinicalRecordState, projected: ClinicalRecordState) -> bool:
    if stored.privacy in UNPROJECTABLE_PRIVACY:
        return (stored.annotation, stored.release) == (
            projected.annotation,
            projected.release,
        )
    return stored == projected


def _locked_record_facts(
    kind: ClinicalRecordKind, record_id: int
) -> ClinicalRecordFacts:
    """Serialize fact and journal mutations even before the first journal row exists."""
    match kind:
        case ClinicalRecordKind.VIDEO:
            video = (
                VideoFile.objects.select_for_update(of=("self",))
                .select_related("state", "sensitive_meta")
                .get(pk=record_id)
            )
            return video_record_facts(video)
        case ClinicalRecordKind.REPORT:
            report = (
                RawPdfFile.objects.select_for_update(of=("self",))
                .select_related("state", "sensitive_meta")
                .get(pk=record_id)
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


def stored_workflow_rows(
    kind: ClinicalRecordKind, record_ids: Collection[int]
) -> Mapping[int, ClinicalWorkflowRecord]:
    """Stored rows of ``record_ids`` keyed by record id, in one query.

    Batch readers pass each row (or ``None``) to :func:`resolve_workflow_state`.
    """
    rows = ClinicalWorkflowRecord.objects.filter(
        kind=kind.value, record_id__in=list(record_ids)
    )
    return {int(row.record_id): row for row in rows}


def resolve_workflow_state(
    kind: ClinicalRecordKind,
    record_id: int,
    facts: ClinicalRecordFacts,
    row: ClinicalWorkflowRecord | None,
) -> WorkflowSnapshot:
    """The one rule every reader uses: facts project, a stored row must agree.

    Raises :class:`ClinicalLifecycleInvariantError` for inconsistent facts and
    :class:`WorkflowStateDriftError` when a stored row disagrees; neither is
    ever mapped to a state.
    """
    projected = derive_clinical_record_state(facts)
    if row is None:
        return WorkflowSnapshot(kind, record_id, projected, 0, StateSource.PROJECTED)
    stored = stored_state(row)
    if not _agrees(stored, projected):
        emit_structured_event(
            logger,
            "clinical_workflow.state_drift",
            level=logging.ERROR,
            kind=kind.value,
            record_id=record_id,
            stored_privacy=stored.privacy.value,
            stored_annotation=stored.annotation.value,
            stored_release=stored.release.value,
            projected_privacy=projected.privacy.value,
            projected_annotation=projected.annotation.value,
            projected_release=projected.release.value,
            version=int(row.version),
        )
        raise WorkflowStateDriftError(
            kind=kind, record_id=record_id, stored=stored, projected=projected
        )
    return WorkflowSnapshot(kind, record_id, stored, row.version, StateSource.STORED)


def read_workflow_state(
    kind: ClinicalRecordKind, record_id: int, facts: ClinicalRecordFacts
) -> WorkflowSnapshot:
    """Current state of one record; see :func:`resolve_workflow_state`."""
    row = ClinicalWorkflowRecord.objects.filter(
        kind=kind.value, record_id=record_id
    ).first()
    return resolve_workflow_state(kind, record_id, facts, row)


def require_workflow_transition(
    kind: ClinicalRecordKind,
    record_id: int,
    facts: ClinicalRecordFacts,
    event: ClinicalLifecycleEvent,
) -> ClinicalRecordState:
    """Target of ``event`` from the same state the workflow API reports.

    Gates such as export approval call this instead of re-projecting facts,
    so a stored row and the gate can never disagree. Nothing is written.
    """
    current = read_workflow_state(kind, record_id, facts).state
    return guarded_clinical_transition(current, facts, event)


def advance_workflow_if_journaled(
    kind: ClinicalRecordKind, record_id: int, event: ClinicalLifecycleEvent
) -> None:
    """Admit a fact mutation under its owner's transaction; advance existing journals."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("A clinical fact mutation requires an atomic transaction")
    facts = _locked_record_facts(kind, record_id)
    row = (
        ClinicalWorkflowRecord.objects.select_for_update()
        .filter(kind=kind.value, record_id=record_id)
        .first()
    )
    current = resolve_workflow_state(kind, record_id, facts, row).state
    target = guarded_clinical_transition(current, facts, event)
    if row is not None and target != current:
        _persist_transition(row, current, target, event, uuid4().hex, None)


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
        StateSource.STORED,
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
    facts = _locked_record_facts(kind, record_id)
    row = _seeded_row(kind, record_id, facts)
    current = resolve_workflow_state(kind, record_id, facts, row).state
    target = guarded_clinical_transition(current, facts, event)
    return _persist_transition(row, current, target, event, idempotency_key, actor)


def _persist_transition(
    row: ClinicalWorkflowRecord,
    current: ClinicalRecordState,
    target: ClinicalRecordState,
    event: ClinicalLifecycleEvent,
    idempotency_key: str,
    actor: models.Model | None,
) -> WorkflowTransitionResult:
    kind, record_id = ClinicalRecordKind(row.kind), row.record_id
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
        kind, record_id, target, row.version + 1, StateSource.STORED
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
    ``replayed=True``; a key reused for another event raises. The caller
    commits the fact change that realizes the event in the same transaction;
    otherwise later reads fail closed with :class:`WorkflowStateDriftError`.
    Other side effects run after commit.
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
