from __future__ import annotations

from dataclasses import replace
from enum import StrEnum

import pytest

from endoreg_db.services.clinical_lifecycle import (
    AnnotationStage as A,
    ClinicalLifecycleEvent as Event,
    ClinicalLifecycleInvariantError,
    ClinicalLifecycleTransitionError,
    ClinicalRecordFacts,
    ClinicalRecordKind,
    ClinicalRecordState,
    ExportApproval,
    LifecycleInvariantViolation as Violation,
    PrivacyStage as P,
    ReleaseStage as R,
    TransitionRejection as Rejection,
    derive_clinical_record_state,
    evidence_rejection,
    require_clinical_transition,
    transition_clinical_record,
    transition_tables,
)

pytestmark = pytest.mark.no_db

_INACTIVE = {P.FAILED, P.LOST, P.WITHDRAWN}


def _facts(**overrides: object) -> ClinicalRecordFacts:
    base = ClinicalRecordFacts(
        kind=ClinicalRecordKind.VIDEO,
        record_id=1,
        integrity_lost=False,
        processing_failed=False,
        processing_started=False,
        anonymized_flag=False,
        processed_artifact_present=False,
        anonymization_validated=False,
        identity_verified=False,
        legacy_identity_exemption=False,
        raw_source_present=True,
        direct_identifiers_erased=False,
        medical_annotation_complete=False,
        export_approval=ExportApproval.NONE,
        dataset_member=False,
    )
    return replace(base, **overrides)  # pyright: ignore[reportArgumentType]


_PURGED = {
    "anonymized_flag": True,
    "processed_artifact_present": True,
    "anonymization_validated": True,
    "identity_verified": True,
    "raw_source_present": False,
    "direct_identifiers_erased": True,
}


def test_every_event_belongs_to_exactly_one_track() -> None:
    privacy, annotation, release = transition_tables()
    tracks = [set(privacy), set(annotation), set(release)]
    assert set().union(*tracks) == set(Event)
    assert sum(len(track) for track in tracks) == len(Event)
    for table in (privacy, annotation, release):
        for event, rows in table.items():
            assert rows, f"{event.value} has no permitted source"


def _all_states() -> list[ClinicalRecordState]:
    return [ClinicalRecordState(p, a, r) for p in P for a in A for r in R]


@pytest.mark.parametrize("event", list(Event))
def test_matrix_is_total_and_deterministic(event: Event) -> None:
    """Every (state, event) pair either yields one state or a typed rejection."""
    for state in _all_states():
        try:
            first = transition_clinical_record(state, event)
        except ClinicalLifecycleTransitionError as exc:
            assert isinstance(exc.reason, StrEnum)
            continue
        assert transition_clinical_record(state, event) == first


def test_happy_path_from_import_to_dataset_ingestion() -> None:
    path = (
        (Event.IMPORT_STARTED, P.IMPORTING, A.NOT_AVAILABLE, R.NOT_RELEASED),
        (
            Event.ARTIFACT_PUBLISHED,
            P.AWAITING_VALIDATION,
            A.IN_PROGRESS,
            R.NOT_RELEASED,
        ),
        (
            Event.ANNOTATION_COMPLETED,
            P.AWAITING_VALIDATION,
            A.VALIDATED,
            R.NOT_RELEASED,
        ),
        (Event.ANONYMIZATION_VALIDATED, P.VALIDATED, A.VALIDATED, R.NOT_RELEASED),
        (Event.RAW_PURGED, P.RAW_PURGED, A.VALIDATED, R.NOT_RELEASED),
        (Event.EXPORT_APPROVED, P.RAW_PURGED, A.VALIDATED, R.EXPORT_READY),
        (Event.EXPORTED, P.RAW_PURGED, A.VALIDATED, R.EXPORTED),
        (Event.DATASET_INGESTED, P.RAW_PURGED, A.VALIDATED, R.DATASET_INGESTED),
    )
    state = ClinicalRecordState(P.IMPORT_PENDING, A.NOT_AVAILABLE, R.NOT_RELEASED)
    for event, privacy, annotation, release in path:
        state = transition_clinical_record(state, event)
        assert state == ClinicalRecordState(privacy, annotation, release), event.value
        assert transition_clinical_record(state, event) == state, event.value


@pytest.mark.parametrize(
    ("privacy", "annotation", "reason"),
    [
        (P.VALIDATED, A.VALIDATED, Rejection.PRIVACY_TRACK_INCOMPLETE),
        (P.AWAITING_VALIDATION, A.VALIDATED, Rejection.PRIVACY_TRACK_INCOMPLETE),
        (P.RAW_PURGED, A.IN_PROGRESS, Rejection.ANNOTATION_TRACK_INCOMPLETE),
    ],
)
def test_export_requires_both_tracks_complete(
    privacy: P, annotation: A, reason: Rejection
) -> None:
    state = ClinicalRecordState(privacy, annotation, R.NOT_RELEASED)
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        transition_clinical_record(state, Event.EXPORT_APPROVED)
    assert exc_info.value.reason is reason


@pytest.mark.parametrize("privacy", [P.VALIDATED, P.RAW_PURGED])
def test_validation_is_final(privacy: P) -> None:
    state = ClinicalRecordState(privacy, A.IN_PROGRESS, R.NOT_RELEASED)
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        transition_clinical_record(state, Event.CORRECTION_REQUESTED)
    assert exc_info.value.reason is Rejection.VALIDATION_IS_FINAL


@pytest.mark.parametrize("privacy", [P.IMPORTING, P.AWAITING_VALIDATION])
def test_raw_purge_requires_validation(privacy: P) -> None:
    with pytest.raises(ClinicalLifecycleTransitionError):
        transition_clinical_record(
            ClinicalRecordState(privacy, A.IN_PROGRESS, R.NOT_RELEASED),
            Event.RAW_PURGED,
        )


def test_reopening_annotation_revokes_export_readiness_but_not_after_release() -> None:
    ready = ClinicalRecordState(P.RAW_PURGED, A.VALIDATED, R.EXPORT_READY)
    assert transition_clinical_record(ready, Event.ANNOTATION_REOPENED) == (
        ClinicalRecordState(P.RAW_PURGED, A.IN_PROGRESS, R.NOT_RELEASED)
    )
    exported = replace(ready, release=R.EXPORTED)
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        transition_clinical_record(exported, Event.ANNOTATION_REOPENED)
    assert exc_info.value.reason is Rejection.ALREADY_RELEASED


@pytest.mark.parametrize("state", _all_states())
def test_consent_withdrawal_is_reachable_from_every_state(
    state: ClinicalRecordState,
) -> None:
    withdrawn = transition_clinical_record(state, Event.CONSENT_WITHDRAWN)
    assert withdrawn.privacy is P.WITHDRAWN
    assert withdrawn.release is R.NOT_RELEASED


@pytest.mark.parametrize("privacy", sorted(_INACTIVE))
@pytest.mark.parametrize(
    "event",
    [
        Event.ANNOTATION_COMPLETED,
        Event.EXPORT_APPROVED,
        Event.EXPORTED,
        Event.DATASET_INGESTED,
        Event.ARTIFACT_PUBLISHED,
    ],
)
def test_inactive_records_accept_no_progress(privacy: P, event: Event) -> None:
    state = ClinicalRecordState(privacy, A.VALIDATED, R.EXPORT_READY)
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        transition_clinical_record(state, event)
    assert exc_info.value.reason is Rejection.RECORD_INACTIVE


def test_lost_and_withdrawn_never_recover_implicitly() -> None:
    lost = ClinicalRecordState(P.LOST, A.IN_PROGRESS, R.NOT_RELEASED)
    assert transition_clinical_record(lost, Event.RECONCILE_RETRY).privacy is (
        P.IMPORT_PENDING
    )
    withdrawn = replace(lost, privacy=P.WITHDRAWN)
    for event in (Event.RECONCILE_RETRY, Event.RETRY_REQUESTED, Event.IMPORT_STARTED):
        with pytest.raises(ClinicalLifecycleTransitionError):
            transition_clinical_record(withdrawn, event)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, (P.IMPORT_PENDING, A.NOT_AVAILABLE, R.NOT_RELEASED)),
        ({"processing_started": True}, (P.IMPORTING, A.NOT_AVAILABLE, R.NOT_RELEASED)),
        (
            {"anonymized_flag": True, "processed_artifact_present": True},
            (P.AWAITING_VALIDATION, A.IN_PROGRESS, R.NOT_RELEASED),
        ),
        (
            {
                "anonymized_flag": True,
                "processed_artifact_present": True,
                "anonymization_validated": True,
                "identity_verified": True,
            },
            (P.VALIDATED, A.IN_PROGRESS, R.NOT_RELEASED),
        ),
        (
            {**_PURGED, "raw_source_present": True},
            (P.VALIDATED, A.IN_PROGRESS, R.NOT_RELEASED),
        ),
        (_PURGED, (P.RAW_PURGED, A.IN_PROGRESS, R.NOT_RELEASED)),
        (
            {
                **_PURGED,
                "medical_annotation_complete": True,
                "export_approval": ExportApproval.STALE,
            },
            (P.RAW_PURGED, A.VALIDATED, R.NOT_RELEASED),
        ),
        (
            {
                **_PURGED,
                "medical_annotation_complete": True,
                "export_approval": ExportApproval.CURRENT,
            },
            (P.RAW_PURGED, A.VALIDATED, R.EXPORT_READY),
        ),
        (
            {**_PURGED, "medical_annotation_complete": True, "dataset_member": True},
            (P.RAW_PURGED, A.VALIDATED, R.DATASET_INGESTED),
        ),
        (
            {"processing_failed": True, "anonymized_flag": True},
            (P.FAILED, A.NOT_AVAILABLE, R.NOT_RELEASED),
        ),
        (
            {"integrity_lost": True, "processing_failed": True},
            (P.LOST, A.NOT_AVAILABLE, R.NOT_RELEASED),
        ),
    ],
)
def test_derivation_projects_one_state(
    overrides: dict[str, object], expected: tuple[P, A, R]
) -> None:
    assert derive_clinical_record_state(_facts(**overrides)) == ClinicalRecordState(
        *expected
    )


@pytest.mark.parametrize(
    ("overrides", "violation"),
    [
        ({"anonymized_flag": True}, Violation.ANONYMIZED_WITHOUT_ARTIFACT),
        ({"anonymization_validated": True}, Violation.VALIDATED_WITHOUT_ARTIFACT),
        (
            {
                "anonymized_flag": True,
                "processed_artifact_present": True,
                "anonymization_validated": True,
            },
            Violation.VALIDATED_WITHOUT_VERIFIED_IDENTITY,
        ),
        (
            {"direct_identifiers_erased": True},
            Violation.IDENTIFIERS_ERASED_BEFORE_VERIFICATION,
        ),
        (
            {
                **_PURGED,
                "raw_source_present": True,
                "medical_annotation_complete": True,
                "export_approval": ExportApproval.CURRENT,
            },
            Violation.RELEASED_BEFORE_RAW_PURGE,
        ),
        (
            {**_PURGED, "dataset_member": True},
            Violation.RELEASED_WITHOUT_ANNOTATION_VALIDATION,
        ),
        (
            {
                "anonymized_flag": True,
                "processed_artifact_present": True,
                "identity_verified": True,
                "dataset_member": True,
            },
            Violation.RELEASED_WITHOUT_ANONYMIZATION_VALIDATION,
        ),
    ],
)
def test_impossible_fact_combinations_fail_loudly(
    overrides: dict[str, object], violation: Violation
) -> None:
    with pytest.raises(ClinicalLifecycleInvariantError) as exc_info:
        derive_clinical_record_state(_facts(**overrides))
    assert violation in exc_info.value.violations


def test_validation_requires_verified_identity() -> None:
    awaiting = _facts(anonymized_flag=True, processed_artifact_present=True)
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        require_clinical_transition(awaiting, Event.ANONYMIZATION_VALIDATED)
    assert exc_info.value.reason is Rejection.IDENTITY_NOT_VERIFIED
    verified = replace(awaiting, identity_verified=True)
    assert require_clinical_transition(
        verified, Event.ANONYMIZATION_VALIDATED
    ).privacy is (P.VALIDATED)


def test_reports_cannot_be_released_without_an_annotation_axis() -> None:
    report = _facts(kind=ClinicalRecordKind.REPORT, **_PURGED)
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        require_clinical_transition(report, Event.EXPORT_APPROVED)
    assert exc_info.value.reason is Rejection.ANNOTATION_TRACK_INCOMPLETE


_LEGACY_VALIDATED = {
    "anonymized_flag": True,
    "processed_artifact_present": True,
    "anonymization_validated": True,
    "legacy_identity_exemption": True,
}


@pytest.mark.parametrize(
    ("overrides", "privacy"),
    [
        (_LEGACY_VALIDATED, P.VALIDATED),
        (
            {
                **_LEGACY_VALIDATED,
                "raw_source_present": False,
                "direct_identifiers_erased": True,
            },
            P.RAW_PURGED,
        ),
    ],
)
def test_legacy_exemption_replaces_identity_evidence_for_validated_videos(
    overrides: dict[str, object], privacy: P
) -> None:
    assert derive_clinical_record_state(_facts(**overrides)).privacy is privacy


def test_legacy_exemption_on_unvalidated_video_fails_loudly() -> None:
    with pytest.raises(ClinicalLifecycleInvariantError) as exc_info:
        derive_clinical_record_state(
            _facts(
                anonymized_flag=True,
                processed_artifact_present=True,
                legacy_identity_exemption=True,
            )
        )
    assert Violation.LEGACY_EXEMPTION_WITHOUT_VALIDATION in exc_info.value.violations


def test_legacy_exemption_never_authorizes_a_new_validation() -> None:
    facts = _facts(anonymized_flag=True, processed_artifact_present=True)
    exempt = replace(facts, legacy_identity_exemption=True)
    assert evidence_rejection(exempt, Event.ANONYMIZATION_VALIDATED) is (
        Rejection.IDENTITY_NOT_VERIFIED
    )
    assert (
        evidence_rejection(
            replace(facts, identity_verified=True), Event.ANONYMIZATION_VALIDATED
        )
        is None
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({}, Rejection.PRIVACY_TRACK_INCOMPLETE),
        (
            {"raw_source_present": False, "direct_identifiers_erased": True},
            None,
        ),
    ],
)
def test_legacy_exemption_does_not_waive_purge_before_export(
    overrides: dict[str, object], reason: Rejection | None
) -> None:
    facts = _facts(**_LEGACY_VALIDATED, medical_annotation_complete=True, **overrides)
    if reason is None:
        assert require_clinical_transition(facts, Event.EXPORT_APPROVED).release is (
            R.EXPORT_READY
        )
        return
    with pytest.raises(ClinicalLifecycleTransitionError) as exc_info:
        require_clinical_transition(facts, Event.EXPORT_APPROVED)
    assert exc_info.value.reason is reason
