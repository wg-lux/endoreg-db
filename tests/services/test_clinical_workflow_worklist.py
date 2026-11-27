from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from lx_dtypes.models.contracts.media_workflow_api import StageCount, StageCounts
from pydantic import ValidationError

from endoreg_db.models import Center, RawPdfFile, VideoFile, VideoState
from endoreg_db.services.media.state import update_media_state
from tests.helpers.media_states import create_media_state
from endoreg_db.models.state.clinical_workflow import ClinicalWorkflowRecord
from endoreg_db.services.clinical_lifecycle import worklist
from endoreg_db.services.clinical_lifecycle.state_machine import (
    AnnotationStage,
    ClinicalLifecycleEvent,
    ClinicalRecordFacts,
    ClinicalRecordKind,
    ClinicalRecordState,
    ExportApproval,
    PrivacyStage,
    ReleaseStage,
    derive_clinical_record_state,
)
from endoreg_db.services.clinical_lifecycle.worklist import (
    WorklistStage,
    WorklistStageCounts,
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


_PURGED = {
    "anonymization_validated": True,
    "identity_verified": True,
    "raw_source_present": False,
    "direct_identifiers_erased": True,
    "medical_annotation_complete": True,
}


def _video_with_stored_projection(
    monkeypatch: pytest.MonkeyPatch, facts: ClinicalRecordFacts
) -> ClinicalRecordState:
    """A stored row that agrees with ``facts``, the only consistent stored row."""
    center = Center.objects.create(name=f"worklist-{uuid4().hex[:8]}")
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"worklist-{uuid4().hex[:8]}",
        state=VideoState.objects.create(),
    )
    facts = replace(facts, record_id=int(video.pk))

    def fixed_facts(_video: VideoFile) -> ClinicalRecordFacts:
        return facts

    monkeypatch.setattr(worklist, "video_record_facts", fixed_facts)
    state = derive_clinical_record_state(facts)
    ClinicalWorkflowRecord.objects.create(
        kind="video",
        record_id=video.pk,
        privacy=state.privacy.value,
        annotation=state.annotation.value,
        release=state.release.value,
    )
    return state


def _counts() -> tuple[dict[WorklistStage, tuple[int, int]], int]:
    result = stage_counts(VideoFile.objects.all(), RawPdfFile.objects.none())
    return {
        entry.stage: (entry.count, entry.transient) for entry in result.stages.stages
    }, result.unmappable


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "state", "expected"),
    [
        (
            {},
            (P.AWAITING_VALIDATION, A.IN_PROGRESS, R.NOT_RELEASED),
            {WorklistStage.VALIDATE: (1, 0), WorklistStage.ANNOTATE: (1, 0)},
        ),
        (
            {"anonymized_flag": False, "processed_artifact_present": False},
            (P.IMPORTING, A.NOT_AVAILABLE, R.NOT_RELEASED),
            {WorklistStage.VALIDATE: (0, 1), WorklistStage.ANNOTATE: (0, 1)},
        ),
        (
            _PURGED,
            (P.RAW_PURGED, A.VALIDATED, R.NOT_RELEASED),
            {WorklistStage.APPROVE_EXPORT: (1, 0)},
        ),
        (
            {**_PURGED, "raw_source_present": True},
            (P.VALIDATED, A.VALIDATED, R.NOT_RELEASED),
            {WorklistStage.APPROVE_EXPORT: (0, 1)},
        ),
        (
            {**_PURGED, "export_approval": ExportApproval.CURRENT},
            (P.RAW_PURGED, A.VALIDATED, R.EXPORT_READY),
            {},
        ),
        (
            {"integrity_lost": True},
            (P.LOST, A.IN_PROGRESS, R.NOT_RELEASED),
            {},
        ),
    ],
)
def test_stage_counts_follow_resolved_stored_state(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    state: tuple[P, A, R],
    expected: dict[WorklistStage, tuple[int, int]],
) -> None:
    stored = _video_with_stored_projection(monkeypatch, replace(FACTS, **overrides))
    assert stored == ClinicalRecordState(*state)

    counts, unmappable = _counts()

    assert unmappable == 0
    assert list(counts) == list(WorklistStage)
    for stage in WorklistStage:
        assert counts[stage] == expected.get(stage, (0, 0))


@pytest.mark.django_db
def test_stored_row_disagreeing_with_facts_is_unmappable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _video_with_stored_projection(monkeypatch, FACTS)
    ClinicalWorkflowRecord.objects.update(release=R.EXPORT_READY.value)

    counts, unmappable = _counts()

    assert unmappable == 1
    assert all(value == (0, 0) for value in counts.values())


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
    state = create_media_state(VideoState, anonymized=True)
    update_media_state(
        VideoState.objects.get(pk=state.pk),
        changes=dict(anonymization_validated=True),
    )
    VideoFile.objects.create(
        center=center, raw_video_hash=f"worklist-{uuid4().hex[:8]}", state=state
    )

    counts, unmappable = _counts()

    assert unmappable == 1
    assert all(value == (0, 0) for value in counts.values())


def _contract_stages(rows: tuple[StageCount, ...]) -> StageCounts:
    return StageCounts(stages=rows)


_ZERO_ROWS = tuple(
    StageCount(stage=stage, count=0, transient=0) for stage in WorklistStage
)


@pytest.mark.parametrize("unmappable", [0, 3])
def test_worklist_counts_pair_contract_rows_with_unmappable(unmappable: int) -> None:
    counts = WorklistStageCounts(
        stages=_contract_stages(_ZERO_ROWS), unmappable=unmappable
    )

    assert counts.unmappable == unmappable
    assert [row.stage for row in counts.stages.stages] == list(WorklistStage)


def test_negative_unmappable_is_rejected() -> None:
    with pytest.raises(ValueError, match="unmappable"):
        WorklistStageCounts(stages=_contract_stages(_ZERO_ROWS), unmappable=-1)


@pytest.mark.parametrize(
    "rows",
    [
        _ZERO_ROWS[:-1],
        (*_ZERO_ROWS, _ZERO_ROWS[0]),
    ],
    ids=["missing_stage", "duplicate_stage"],
)
def test_contract_rejects_incomplete_stage_rows(rows: tuple[StageCount, ...]) -> None:
    with pytest.raises(ValidationError):
        _contract_stages(rows)
