from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from endoreg_db.models import Center, RawPdfFile, VideoFile, VideoState
from endoreg_db.models.state.clinical_workflow import ClinicalWorkflowRecord
from endoreg_db.services.clinical_lifecycle.state_machine import (
    AnnotationStage,
    ClinicalLifecycleEvent,
    ClinicalRecordFacts,
    ClinicalRecordKind,
    ClinicalRecordState,
    ExportApproval,
    PrivacyStage,
    ReleaseStage,
)
from endoreg_db.services.clinical_lifecycle.worklist import (
    WorklistStage,
    allowed_events,
    stage_counts,
)

E = ClinicalLifecycleEvent
P = PrivacyStage
A = AnnotationStage
R = ReleaseStage

FACTS = ClinicalRecordFacts(
    kind=ClinicalRecordKind.VIDEO,
    record_id=1,
    integrity_lost=False,
    processing_failed=False,
    processing_started=True,
    anonymized_flag=True,
    processed_artifact_present=True,
    anonymization_validated=False,
    identity_verified=False,
    legacy_identity_exemption=False,
    raw_source_present=True,
    direct_identifiers_erased=False,
    medical_annotation_complete=False,
    export_approval=ExportApproval.NONE,
    dataset_member=False,
)


def _allowed(
    state: ClinicalRecordState, facts: ClinicalRecordFacts = FACTS
) -> tuple[ClinicalLifecycleEvent, ...]:
    return allowed_events(state, facts)


AWAITING = ClinicalRecordState(P.AWAITING_VALIDATION, A.IN_PROGRESS, R.NOT_RELEASED)


def test_unverified_identity_hides_validation() -> None:
    events = _allowed(AWAITING)

    assert E.ANONYMIZATION_VALIDATED not in events
    assert E.CORRECTION_REQUESTED in events
    assert E.RAW_PURGED not in events


def test_verified_identity_allows_validation() -> None:
    assert E.ANONYMIZATION_VALIDATED in _allowed(
        AWAITING, replace(FACTS, identity_verified=True)
    )


def test_legacy_exemption_never_satisfies_new_validation() -> None:
    assert E.ANONYMIZATION_VALIDATED not in _allowed(
        AWAITING, replace(FACTS, legacy_identity_exemption=True)
    )


def test_export_approval_waits_for_raw_purge() -> None:
    assert E.EXPORT_APPROVED not in _allowed(
        ClinicalRecordState(P.VALIDATED, A.VALIDATED, R.NOT_RELEASED)
    )
    assert E.EXPORT_APPROVED in _allowed(
        ClinicalRecordState(P.RAW_PURGED, A.VALIDATED, R.NOT_RELEASED)
    )


@pytest.mark.parametrize("privacy", [P.VALIDATED, P.RAW_PURGED])
def test_no_correction_after_validation(privacy: PrivacyStage) -> None:
    events = _allowed(ClinicalRecordState(privacy, A.IN_PROGRESS, R.NOT_RELEASED))

    assert E.CORRECTION_REQUESTED not in events


@pytest.mark.parametrize("privacy", [P.FAILED, P.LOST, P.WITHDRAWN])
def test_inactive_records_allow_no_annotation_or_release(privacy: PrivacyStage) -> None:
    events = _allowed(ClinicalRecordState(privacy, A.IN_PROGRESS, R.NOT_RELEASED))

    assert not {E.ANNOTATION_COMPLETED, E.EXPORT_APPROVED} & set(events)


def _video_with_state(privacy: P, annotation: A, release: R) -> VideoFile:
    center = Center.objects.create(name=f"worklist-{uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"worklist-{uuid4().hex[:8]}",
        state=VideoState.objects.create(),
    )
    ClinicalWorkflowRecord.objects.create(
        kind="video",
        record_id=video.pk,
        privacy=privacy.value,
        annotation=annotation.value,
        release=release.value,
    )
    return video


def _counts():
    result = stage_counts(VideoFile.objects.all(), RawPdfFile.objects.none())
    return {
        entry.stage: (entry.count, entry.transient) for entry in result.stages
    }, result.unmappable


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            (P.AWAITING_VALIDATION, A.IN_PROGRESS, R.NOT_RELEASED),
            {WorklistStage.VALIDATE: (1, 0), WorklistStage.ANNOTATE: (1, 0)},
        ),
        (
            (P.IMPORTING, A.NOT_AVAILABLE, R.NOT_RELEASED),
            {WorklistStage.VALIDATE: (0, 1), WorklistStage.ANNOTATE: (0, 1)},
        ),
        (
            (P.RAW_PURGED, A.VALIDATED, R.NOT_RELEASED),
            {WorklistStage.APPROVE_EXPORT: (1, 0)},
        ),
        (
            (P.VALIDATED, A.VALIDATED, R.NOT_RELEASED),
            {WorklistStage.APPROVE_EXPORT: (0, 1)},
        ),
        ((P.RAW_PURGED, A.VALIDATED, R.EXPORT_READY), {}),
        ((P.LOST, A.IN_PROGRESS, R.NOT_RELEASED), {}),
    ],
)
def test_stage_counts_follow_stored_state(
    state: tuple[P, A, R], expected: dict[WorklistStage, tuple[int, int]]
) -> None:
    _video_with_state(*state)

    counts, unmappable = _counts()

    assert unmappable == 0
    assert list(counts) == list(WorklistStage)
    for stage in WorklistStage:
        assert counts[stage] == expected.get(stage, (0, 0))


@pytest.mark.django_db
def test_unstored_record_is_projected_from_facts() -> None:
    center = Center.objects.create(name=f"worklist-{uuid4().hex[:8]}")
    VideoFile.objects.create(
        center=center,
        raw_video_hash=f"worklist-{uuid4().hex[:8]}",
        state=VideoState.objects.create(),
    )

    counts, unmappable = _counts()

    assert unmappable == 0
    assert counts[WorklistStage.VALIDATE] == (0, 1)


@pytest.mark.django_db
def test_inconsistent_record_is_unmappable_not_counted() -> None:
    center = Center.objects.create(name=f"worklist-{uuid4().hex[:8]}")
    state = VideoState.objects.create(anonymized=True)
    VideoState.objects.filter(pk=state.pk).update(anonymization_validated=True)
    VideoFile.objects.create(
        center=center, raw_video_hash=f"worklist-{uuid4().hex[:8]}", state=state
    )

    counts, unmappable = _counts()

    assert unmappable == 1
    assert all(value == (0, 0) for value in counts.values())
