"""End-to-end clinical record lifecycle as a deterministic finite state machine.

One imported video or report runs two tracks that join at release:

* the privacy track: import and anonymization, human validation (which requires
  verified first name, last name and date of birth), then the eager purge of the
  raw source and of direct identifiers (GDPR deletion point 1). Validation is
  final: no correction or repair is accepted after it;
* the annotation track: medical annotation starts as soon as an anonymized
  artifact is published and ends with validated annotations;
* the release stage: export approval, export and dataset ingestion. Approval
  requires a purged privacy track and validated annotations.

Consent withdrawal moves the record to ``WITHDRAWN`` from any state (GDPR
deletion point 3). ``LOST`` never returns automatically.

This module exposes the native reducer and projection from persisted facts. It
does not persist a second global status; database, filesystem and audit side
effects stay at service boundaries and run only after
``persistence.require_workflow_transition`` or ``persistence.apply_workflow_event``
accepted an event through :func:`guarded_clinical_transition`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

# lx-dtypes exposes the public wire contract. Runtime decisions come from Rust.
from lx_dtypes.models.contracts.media_workflow import (
    AnnotationStage,
    ExportApproval,
    PrivacyStage,
    ReleaseStage,
    TransitionRejection,
)
from lx_dtypes.models.contracts.media_workflow import (
    MediaKind as ClinicalRecordKind,
)
from lx_dtypes.models.contracts.media_workflow import (
    WorkflowEvent as ClinicalLifecycleEvent,
)

from endoreg_db import endoreg_rust_backend as native


class LifecycleInvariantViolation(StrEnum):
    ANONYMIZED_WITHOUT_ARTIFACT = "anonymized_without_artifact"
    VALIDATED_WITHOUT_ARTIFACT = "validated_without_artifact"
    VALIDATED_WITHOUT_VERIFIED_IDENTITY = "validated_without_verified_identity"
    IDENTIFIERS_ERASED_BEFORE_VERIFICATION = "identifiers_erased_before_verification"
    LEGACY_EXEMPTION_WITHOUT_VALIDATION = "legacy_exemption_without_validation"
    RELEASED_WITHOUT_ANONYMIZATION_VALIDATION = (
        "released_without_anonymization_validation"
    )
    RELEASED_BEFORE_RAW_PURGE = "released_before_raw_purge"
    RELEASED_WITHOUT_ANNOTATION_VALIDATION = "released_without_annotation_validation"


@dataclass(frozen=True, slots=True)
class ClinicalRecordState:
    privacy: PrivacyStage
    annotation: AnnotationStage
    release: ReleaseStage


@dataclass(frozen=True, slots=True)
class ClinicalRecordFacts:
    """Typed snapshot of the persisted axes; built once at the ORM boundary."""

    kind: ClinicalRecordKind
    record_id: int
    integrity_lost: bool
    processing_failed: bool
    processing_started: bool
    anonymized_flag: bool
    processed_artifact_present: bool
    anonymization_validated: bool
    identity_verified: bool
    # Recorded operator exception for videos validated before identity
    # verification was required; never authorizes a new validation.
    legacy_identity_exemption: bool
    raw_source_present: bool
    direct_identifiers_erased: bool
    medical_annotation_complete: bool
    export_approval: ExportApproval
    dataset_member: bool


class ClinicalLifecycleTransitionError(ValueError):
    def __init__(
        self,
        *,
        state: ClinicalRecordState,
        event: ClinicalLifecycleEvent,
        reason: TransitionRejection,
    ) -> None:
        self.state = state
        self.event = event
        self.reason = reason
        super().__init__(
            "invalid clinical lifecycle transition: "
            f"{state.privacy.value}/{state.annotation.value}/{state.release.value}"
            f" --{event.value}--> ({reason.value})"
        )


class ClinicalLifecycleInvariantError(ValueError):
    def __init__(
        self,
        *,
        kind: ClinicalRecordKind,
        record_id: int,
        violations: tuple[LifecycleInvariantViolation, ...],
    ) -> None:
        self.kind = kind
        self.record_id = record_id
        self.violations = violations
        super().__init__(
            f"{kind.value} {record_id} violates clinical lifecycle invariants: "
            + ", ".join(violation.value for violation in violations)
        )


def transition_tables() -> tuple[
    Mapping[ClinicalLifecycleEvent, Mapping[PrivacyStage, PrivacyStage]],
    Mapping[ClinicalLifecycleEvent, Mapping[AnnotationStage, AnnotationStage]],
    Mapping[ClinicalLifecycleEvent, Mapping[ReleaseStage, ReleaseStage]],
]:
    """Expose the declarative tables for documentation and exhaustiveness tests."""

    def track[S: StrEnum](
        rows: list[tuple[str, str, str]], stage: type[S]
    ) -> Mapping[ClinicalLifecycleEvent, Mapping[S, S]]:
        result: dict[ClinicalLifecycleEvent, dict[S, S]] = {}
        for event, source, target in rows:
            result.setdefault(ClinicalLifecycleEvent(event), {})[stage(source)] = stage(
                target
            )
        return MappingProxyType(result)

    privacy, annotation, release = native.clinical_workflow_tables()
    return (
        track(privacy, PrivacyStage),
        track(annotation, AnnotationStage),
        track(release, ReleaseStage),
    )


def _state(tokens: tuple[str, str, str]) -> ClinicalRecordState:
    privacy, annotation, release = tokens
    return ClinicalRecordState(
        PrivacyStage(privacy), AnnotationStage(annotation), ReleaseStage(release)
    )


def transition_clinical_record(
    state: ClinicalRecordState, event: ClinicalLifecycleEvent
) -> ClinicalRecordState:
    """Apply one native transition and preserve the typed public error contract."""
    try:
        tokens = native.reduce_clinical_workflow(
            state.privacy, state.annotation, state.release, event
        )
    except ValueError as exc:
        raise ClinicalLifecycleTransitionError(
            state=state, event=event, reason=TransitionRejection(str(exc))
        ) from exc
    return _state(tokens)


def lifecycle_invariant_violations(
    facts: ClinicalRecordFacts,
) -> tuple[LifecycleInvariantViolation, ...]:
    _, violations = native.project_clinical_workflow(facts)
    return tuple(map(LifecycleInvariantViolation, violations))


def derive_clinical_record_state(facts: ClinicalRecordFacts) -> ClinicalRecordState:
    """Project persisted evidence through Rust, preserving every invariant violation."""
    tokens, violations = native.project_clinical_workflow(facts)
    if violations:
        raise ClinicalLifecycleInvariantError(
            kind=facts.kind,
            record_id=facts.record_id,
            violations=tuple(map(LifecycleInvariantViolation, violations)),
        )
    return _state(tokens)


def evidence_rejection(
    facts: ClinicalRecordFacts, event: ClinicalLifecycleEvent
) -> TransitionRejection | None:
    reason = native.clinical_evidence_rejection(
        event, facts.identity_verified, facts.kind is ClinicalRecordKind.REPORT
    )
    return TransitionRejection(reason) if reason is not None else None


def guarded_clinical_transition(
    state: ClinicalRecordState,
    facts: ClinicalRecordFacts,
    event: ClinicalLifecycleEvent,
) -> ClinicalRecordState:
    """Target of ``event`` from ``state`` once ``facts`` pass every guard.

    ``state`` may be stored or projected; inconsistent facts and missing
    evidence reject the event either way.
    """
    violations = lifecycle_invariant_violations(facts)
    if violations:
        raise ClinicalLifecycleInvariantError(
            kind=facts.kind, record_id=facts.record_id, violations=violations
        )
    rejection = evidence_rejection(facts, event)
    if rejection is not None:
        raise ClinicalLifecycleTransitionError(
            state=state, event=event, reason=rejection
        )
    return transition_clinical_record(state, event)


def require_clinical_transition(
    facts: ClinicalRecordFacts, event: ClinicalLifecycleEvent
) -> ClinicalRecordState:
    """Pure reducer check from the projection of ``facts`` (tests only).

    Not a service gate: it ignores the stored workflow row. Services use
    ``persistence.require_workflow_transition``, which resolves the state
    through ``persistence.resolve_workflow_state`` first.
    """
    return guarded_clinical_transition(
        derive_clinical_record_state(facts), facts, event
    )


__all__ = [
    "AnnotationStage",
    "ClinicalLifecycleEvent",
    "ClinicalLifecycleInvariantError",
    "ClinicalLifecycleTransitionError",
    "ClinicalRecordFacts",
    "ClinicalRecordKind",
    "ClinicalRecordState",
    "ExportApproval",
    "LifecycleInvariantViolation",
    "PrivacyStage",
    "ReleaseStage",
    "TransitionRejection",
    "derive_clinical_record_state",
    "evidence_rejection",
    "guarded_clinical_transition",
    "lifecycle_invariant_violations",
    "transition_clinical_record",
    "transition_tables",
]
