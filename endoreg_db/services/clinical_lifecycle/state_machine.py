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

This module is the pure reducer and the projection from persisted facts. It
does not persist a second global status; database, filesystem and audit side
effects stay at service boundaries and run only after
:func:`require_clinical_transition` accepted an event.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class ClinicalRecordKind(StrEnum):
    VIDEO = "video"
    REPORT = "report"


class PrivacyStage(StrEnum):
    IMPORT_PENDING = "import_pending"
    IMPORTING = "importing"
    QUARANTINED = "quarantined"
    AWAITING_VALIDATION = "awaiting_validation"
    # Validated; raw source or direct identifiers still await the eager purge.
    VALIDATED = "validated"
    RAW_PURGED = "raw_purged"
    FAILED = "failed"
    LOST = "lost"
    WITHDRAWN = "withdrawn"


class AnnotationStage(StrEnum):
    NOT_AVAILABLE = "not_available"
    IN_PROGRESS = "in_progress"
    VALIDATED = "validated"


class ReleaseStage(StrEnum):
    NOT_RELEASED = "not_released"
    EXPORT_READY = "export_ready"
    EXPORTED = "exported"
    DATASET_INGESTED = "dataset_ingested"


class ExportApproval(StrEnum):
    NONE = "none"
    CURRENT = "current"
    # Approval exists but is bound to another processed-artifact generation.
    STALE = "stale"


class ClinicalLifecycleEvent(StrEnum):
    IMPORT_STARTED = "import_started"
    QUARANTINED = "quarantined"
    QUARANTINE_RELEASED = "quarantine_released"
    ARTIFACT_PUBLISHED = "artifact_published"
    CORRECTION_REQUESTED = "correction_requested"
    ANONYMIZATION_VALIDATED = "anonymization_validated"
    RAW_PURGED = "raw_purged"
    PROCESSING_FAILED = "processing_failed"
    INTEGRITY_LOST = "integrity_lost"
    RETRY_REQUESTED = "retry_requested"
    RECONCILE_RETRY = "reconcile_retry"
    RECONCILE_FAIL = "reconcile_fail"
    CONSENT_WITHDRAWN = "consent_withdrawn"
    ANNOTATION_COMPLETED = "annotation_completed"
    ANNOTATION_REOPENED = "annotation_reopened"
    EXPORT_APPROVED = "export_approved"
    EXPORT_REVOKED = "export_revoked"
    EXPORTED = "exported"
    DATASET_INGESTED = "dataset_ingested"


class TransitionRejection(StrEnum):
    EVENT_NOT_PERMITTED_IN_STATE = "event_not_permitted_in_state"
    RECORD_INACTIVE = "record_inactive"
    VALIDATION_IS_FINAL = "validation_is_final"
    IDENTITY_NOT_VERIFIED = "identity_not_verified"
    PRIVACY_TRACK_INCOMPLETE = "privacy_track_incomplete"
    ANNOTATION_TRACK_INCOMPLETE = "annotation_track_incomplete"
    ALREADY_RELEASED = "already_released"


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


_P = PrivacyStage
_A = AnnotationStage
_R = ReleaseStage
_E = ClinicalLifecycleEvent

_INACTIVE: Final = frozenset({_P.FAILED, _P.LOST, _P.WITHDRAWN})
_ACTIVE: Final = tuple(stage for stage in _P if stage not in _INACTIVE)
_FINAL_VALIDATION: Final = frozenset({_P.VALIDATED, _P.RAW_PURGED})

# Declarative privacy-track table: event -> {source stage: target stage}.
# Self-transitions make redelivered events idempotent.
_PRIVACY_TRANSITIONS: Final[Mapping[_E, Mapping[_P, _P]]] = MappingProxyType(
    {
        _E.IMPORT_STARTED: {
            _P.IMPORT_PENDING: _P.IMPORTING,
            _P.IMPORTING: _P.IMPORTING,
        },
        _E.QUARANTINED: {_P.IMPORTING: _P.QUARANTINED, _P.QUARANTINED: _P.QUARANTINED},
        _E.QUARANTINE_RELEASED: {_P.QUARANTINED: _P.IMPORTING},
        _E.ARTIFACT_PUBLISHED: {
            _P.IMPORTING: _P.AWAITING_VALIDATION,
            _P.AWAITING_VALIDATION: _P.AWAITING_VALIDATION,
        },
        _E.CORRECTION_REQUESTED: {_P.AWAITING_VALIDATION: _P.IMPORTING},
        _E.ANONYMIZATION_VALIDATED: {
            _P.AWAITING_VALIDATION: _P.VALIDATED,
            _P.VALIDATED: _P.VALIDATED,
        },
        _E.RAW_PURGED: {_P.VALIDATED: _P.RAW_PURGED, _P.RAW_PURGED: _P.RAW_PURGED},
        _E.PROCESSING_FAILED: {
            **{stage: _P.FAILED for stage in _ACTIVE},
            _P.FAILED: _P.FAILED,
        },
        _E.INTEGRITY_LOST: {
            **{stage: _P.LOST for stage in _ACTIVE},
            _P.FAILED: _P.LOST,
            _P.LOST: _P.LOST,
        },
        _E.RETRY_REQUESTED: {_P.FAILED: _P.IMPORT_PENDING},
        _E.RECONCILE_RETRY: {_P.LOST: _P.IMPORT_PENDING},
        _E.RECONCILE_FAIL: {_P.LOST: _P.FAILED},
        _E.CONSENT_WITHDRAWN: {stage: _P.WITHDRAWN for stage in _P},
    }
)

_ANNOTATION_TRANSITIONS: Final[Mapping[_E, Mapping[_A, _A]]] = MappingProxyType(
    {
        _E.ANNOTATION_COMPLETED: {
            _A.IN_PROGRESS: _A.VALIDATED,
            _A.VALIDATED: _A.VALIDATED,
        },
        _E.ANNOTATION_REOPENED: {
            _A.IN_PROGRESS: _A.IN_PROGRESS,
            _A.VALIDATED: _A.IN_PROGRESS,
        },
    }
)

_RELEASE_TRANSITIONS: Final[Mapping[_E, Mapping[_R, _R]]] = MappingProxyType(
    {
        _E.EXPORT_APPROVED: {
            _R.NOT_RELEASED: _R.EXPORT_READY,
            _R.EXPORT_READY: _R.EXPORT_READY,
        },
        _E.EXPORT_REVOKED: {
            _R.EXPORT_READY: _R.NOT_RELEASED,
            _R.NOT_RELEASED: _R.NOT_RELEASED,
        },
        _E.EXPORTED: {_R.EXPORT_READY: _R.EXPORTED, _R.EXPORTED: _R.EXPORTED},
        _E.DATASET_INGESTED: {
            _R.EXPORT_READY: _R.DATASET_INGESTED,
            _R.EXPORTED: _R.DATASET_INGESTED,
            _R.DATASET_INGESTED: _R.DATASET_INGESTED,
        },
    }
)


def transition_tables() -> tuple[
    Mapping[ClinicalLifecycleEvent, Mapping[PrivacyStage, PrivacyStage]],
    Mapping[ClinicalLifecycleEvent, Mapping[AnnotationStage, AnnotationStage]],
    Mapping[ClinicalLifecycleEvent, Mapping[ReleaseStage, ReleaseStage]],
]:
    """Expose the declarative tables for documentation and exhaustiveness tests."""
    return _PRIVACY_TRANSITIONS, _ANNOTATION_TRANSITIONS, _RELEASE_TRANSITIONS


def _lookup[S: StrEnum](
    table: Mapping[_E, Mapping[S, S]],
    source: S,
    *,
    state: ClinicalRecordState,
    event: ClinicalLifecycleEvent,
) -> S:
    target = table[event].get(source)
    if target is not None:
        return target
    if state.privacy in _INACTIVE:
        reason = TransitionRejection.RECORD_INACTIVE
    elif event is _E.CORRECTION_REQUESTED and state.privacy in _FINAL_VALIDATION:
        reason = TransitionRejection.VALIDATION_IS_FINAL
    elif state.release in {_R.EXPORTED, _R.DATASET_INGESTED}:
        reason = TransitionRejection.ALREADY_RELEASED
    else:
        reason = TransitionRejection.EVENT_NOT_PERMITTED_IN_STATE
    raise ClinicalLifecycleTransitionError(state=state, event=event, reason=reason)


def transition_clinical_record(
    state: ClinicalRecordState, event: ClinicalLifecycleEvent
) -> ClinicalRecordState:
    """Apply one pure, deterministic lifecycle transition or raise.

    Guards that depend on persisted evidence beyond the state (verified
    identity) are evaluated by :func:`require_clinical_transition`.
    """

    def reject(reason: TransitionRejection) -> ClinicalLifecycleTransitionError:
        return ClinicalLifecycleTransitionError(state=state, event=event, reason=reason)

    if event in _PRIVACY_TRANSITIONS:
        privacy = _lookup(_PRIVACY_TRANSITIONS, state.privacy, state=state, event=event)
        if event is _E.ARTIFACT_PUBLISHED and state.annotation is _A.NOT_AVAILABLE:
            # The annotation track starts as soon as processed media exist.
            return ClinicalRecordState(privacy, _A.IN_PROGRESS, state.release)
        if event is _E.CONSENT_WITHDRAWN:
            return ClinicalRecordState(privacy, state.annotation, _R.NOT_RELEASED)
        return replace(state, privacy=privacy)

    if state.privacy in _INACTIVE:
        raise reject(TransitionRejection.RECORD_INACTIVE)

    if event in _ANNOTATION_TRANSITIONS:
        if event is _E.ANNOTATION_REOPENED and state.release in {
            _R.EXPORTED,
            _R.DATASET_INGESTED,
        }:
            raise reject(TransitionRejection.ALREADY_RELEASED)
        annotation = _lookup(
            _ANNOTATION_TRANSITIONS, state.annotation, state=state, event=event
        )
        release = (
            _R.NOT_RELEASED
            if annotation is _A.IN_PROGRESS and state.release is _R.EXPORT_READY
            else state.release
        )
        return ClinicalRecordState(state.privacy, annotation, release)

    if event is _E.EXPORT_APPROVED:
        if state.privacy is not _P.RAW_PURGED:
            raise reject(TransitionRejection.PRIVACY_TRACK_INCOMPLETE)
        if state.annotation is not _A.VALIDATED:
            raise reject(TransitionRejection.ANNOTATION_TRACK_INCOMPLETE)
    release = _lookup(_RELEASE_TRANSITIONS, state.release, state=state, event=event)
    return replace(state, release=release)


def lifecycle_invariant_violations(
    facts: ClinicalRecordFacts,
) -> tuple[LifecycleInvariantViolation, ...]:
    """Return every persisted-fact combination the lifecycle cannot represent."""
    violations: list[LifecycleInvariantViolation] = []
    identity_attested = facts.identity_verified or facts.legacy_identity_exemption
    if facts.legacy_identity_exemption and not facts.anonymization_validated:
        violations.append(
            LifecycleInvariantViolation.LEGACY_EXEMPTION_WITHOUT_VALIDATION
        )
    if facts.direct_identifiers_erased and not identity_attested:
        violations.append(
            LifecycleInvariantViolation.IDENTIFIERS_ERASED_BEFORE_VERIFICATION
        )
    if facts.integrity_lost or facts.processing_failed:
        return tuple(violations)

    if facts.anonymized_flag and not facts.processed_artifact_present:
        violations.append(LifecycleInvariantViolation.ANONYMIZED_WITHOUT_ARTIFACT)
    published = facts.anonymized_flag and facts.processed_artifact_present
    if facts.anonymization_validated and not published:
        violations.append(LifecycleInvariantViolation.VALIDATED_WITHOUT_ARTIFACT)
    if facts.anonymization_validated and not identity_attested:
        violations.append(
            LifecycleInvariantViolation.VALIDATED_WITHOUT_VERIFIED_IDENTITY
        )
    released = facts.export_approval is ExportApproval.CURRENT or facts.dataset_member
    if released:
        if not facts.anonymization_validated:
            violations.append(
                LifecycleInvariantViolation.RELEASED_WITHOUT_ANONYMIZATION_VALIDATION
            )
        if facts.raw_source_present or not facts.direct_identifiers_erased:
            violations.append(LifecycleInvariantViolation.RELEASED_BEFORE_RAW_PURGE)
        if not facts.medical_annotation_complete:
            violations.append(
                LifecycleInvariantViolation.RELEASED_WITHOUT_ANNOTATION_VALIDATION
            )
    return tuple(violations)


def _derive_privacy(facts: ClinicalRecordFacts) -> PrivacyStage:
    if facts.integrity_lost:
        return _P.LOST
    if facts.processing_failed:
        return _P.FAILED
    if not (facts.anonymized_flag and facts.processed_artifact_present):
        return _P.IMPORTING if facts.processing_started else _P.IMPORT_PENDING
    if not facts.anonymization_validated:
        return _P.AWAITING_VALIDATION
    if facts.raw_source_present or not facts.direct_identifiers_erased:
        return _P.VALIDATED
    return _P.RAW_PURGED


def derive_clinical_record_state(facts: ClinicalRecordFacts) -> ClinicalRecordState:
    """Project persisted facts onto exactly one lifecycle state or raise.

    ``QUARANTINED`` and ``WITHDRAWN`` have no persisted media-state evidence yet,
    so the projection never produces them; the reducer still enforces them.
    """
    violations = lifecycle_invariant_violations(facts)
    if violations:
        raise ClinicalLifecycleInvariantError(
            kind=facts.kind, record_id=facts.record_id, violations=violations
        )
    published = facts.anonymized_flag and facts.processed_artifact_present
    if not published:
        annotation = _A.NOT_AVAILABLE
    elif facts.medical_annotation_complete:
        annotation = _A.VALIDATED
    else:
        annotation = _A.IN_PROGRESS
    if facts.dataset_member:
        release = _R.DATASET_INGESTED
    elif facts.export_approval is ExportApproval.CURRENT:
        release = _R.EXPORT_READY
    else:
        # A stale approval belongs to another generation and grants nothing.
        release = _R.NOT_RELEASED
    return ClinicalRecordState(_derive_privacy(facts), annotation, release)


def evidence_rejection(
    facts: ClinicalRecordFacts, event: ClinicalLifecycleEvent
) -> TransitionRejection | None:
    """Pure guard over persisted evidence beyond the state itself.

    A new validation needs a human identity verification; a legacy identity
    exemption never satisfies it.
    """
    if event is _E.ANONYMIZATION_VALIDATED and not facts.identity_verified:
        return TransitionRejection.IDENTITY_NOT_VERIFIED
    return None


def require_clinical_transition(
    facts: ClinicalRecordFacts, event: ClinicalLifecycleEvent
) -> ClinicalRecordState:
    """Derive the current state, evaluate evidence guards, return the target."""
    state = derive_clinical_record_state(facts)
    rejection = evidence_rejection(facts, event)
    if rejection is not None:
        raise ClinicalLifecycleTransitionError(
            state=state, event=event, reason=rejection
        )
    return transition_clinical_record(state, event)


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
    "lifecycle_invariant_violations",
    "require_clinical_transition",
    "transition_clinical_record",
    "transition_tables",
]
