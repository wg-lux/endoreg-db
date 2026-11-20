from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest
from django.utils import timezone

from endoreg_db.models import Center, VideoFile, VideoState
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.state.audit_ledger import AuditLedger
from endoreg_db.models.state.clinical_workflow import (
    ClinicalWorkflowRecord,
    ClinicalWorkflowTransition,
)
from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)
from endoreg_db.services.clinical_lifecycle import persistence
from endoreg_db.services.clinical_lifecycle.facts import video_record_facts
from endoreg_db.services.clinical_lifecycle.persistence import (
    AUDIT_ACTION,
    IdempotencyKeyReuseError,
    WorkflowConcurrencyError,
    WorkflowStateSource,
    apply_workflow_event,
    read_workflow_state,
)
from endoreg_db.services.clinical_lifecycle.state_machine import (
    AnnotationStage,
    ClinicalLifecycleEvent,
    ClinicalLifecycleTransitionError,
    ClinicalRecordKind,
    ClinicalRecordState,
    PrivacyStage,
    ReleaseStage,
    TransitionRejection,
)
from endoreg_db.services.patient_identity.verification import (
    update_sensitive_meta_verification,
)

pytestmark = pytest.mark.django_db

VIDEO = ClinicalRecordKind.VIDEO
E = ClinicalLifecycleEvent


def _video(*, state: VideoState, verified: bool, processed: bool) -> VideoFile:
    center = Center.objects.create(name=f"workflow-center-{uuid4().hex[:8]}")
    meta = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Max",
        patient_last_name="Mustermann",
        patient_dob=timezone.make_aware(datetime(1994, 3, 21)),
        examination_date=date(2025, 11, 27),
        patient_gender=Gender.objects.create(name=f"workflow-{uuid4().hex[:8]}"),
    )
    if verified:
        update_sensitive_meta_verification(
            sensitive_meta_id=meta.pk,
            command=SensitiveMetaVerificationCommand(
                dob_verified=True, names_verified=True
            ),
        )
        meta.refresh_from_db()
    video = VideoFile.objects.create(
        center=center,
        raw_video_hash=f"workflow-video-{uuid4().hex[:8]}",
        processed_video_hash=uuid4().hex * 2,
        sensitive_meta=meta,
        state=state,
    )
    if processed:
        video.processed_file.name = "videos/processed/workflow.mp4"
        video.save()
    return video


def _pending_video() -> VideoFile:
    return _video(state=VideoState.objects.create(), verified=False, processed=False)


def _awaiting_validation_video(*, verified: bool) -> VideoFile:
    return _video(
        state=VideoState.objects.create(anonymized=True),
        verified=verified,
        processed=True,
    )


def _apply(video: VideoFile, event: ClinicalLifecycleEvent, key: str):
    return apply_workflow_event(
        kind=VIDEO, record_id=video.pk, event=event, idempotency_key=key
    )


def test_unstored_record_is_reported_as_projection() -> None:
    video = _pending_video()
    snapshot = read_workflow_state(VIDEO, video.pk, video_record_facts(video))

    assert snapshot.source is WorkflowStateSource.PROJECTED
    assert snapshot.version == 0
    assert snapshot.state.privacy is PrivacyStage.IMPORT_PENDING
    assert not ClinicalWorkflowRecord.objects.exists()


def test_accepted_event_seeds_row_writes_transition_and_audit() -> None:
    video = _pending_video()

    result = _apply(video, E.IMPORT_STARTED, "import-1")

    assert result.replayed is False
    assert result.snapshot.version == 1
    assert result.snapshot.state.privacy is PrivacyStage.IMPORTING
    row = ClinicalWorkflowRecord.objects.get(kind="video", record_id=video.pk)
    assert (row.privacy, row.version) == ("importing", 1)
    transition = ClinicalWorkflowTransition.objects.get(record=row)
    assert (transition.from_privacy, transition.to_privacy) == (
        "import_pending",
        "importing",
    )
    assert transition.from_version == 0
    entry = AuditLedger.objects.get(action=AUDIT_ACTION, object_pk=str(row.pk))
    assert entry.data["event"] == "import_started"
    assert (
        read_workflow_state(VIDEO, video.pk, video_record_facts(video)).source
        is WorkflowStateSource.STORED
    )


def test_repeated_key_replays_without_new_rows() -> None:
    video = _pending_video()
    first = _apply(video, E.IMPORT_STARTED, "import-1")

    again = _apply(video, E.IMPORT_STARTED, "import-1")

    assert again.replayed is True
    assert again.snapshot == first.snapshot
    assert ClinicalWorkflowTransition.objects.count() == 1
    assert AuditLedger.objects.filter(action=AUDIT_ACTION).count() == 1


def test_key_reused_for_another_event_fails_loudly() -> None:
    video = _pending_video()
    _apply(video, E.IMPORT_STARTED, "shared-key")

    with pytest.raises(IdempotencyKeyReuseError):
        _apply(video, E.QUARANTINED, "shared-key")
    assert ClinicalWorkflowTransition.objects.count() == 1


def test_rejected_event_writes_nothing() -> None:
    video = _pending_video()

    with pytest.raises(ClinicalLifecycleTransitionError) as rejected:
        _apply(video, E.RAW_PURGED, "purge-1")

    assert rejected.value.reason is TransitionRejection.EVENT_NOT_PERMITTED_IN_STATE
    assert not ClinicalWorkflowRecord.objects.exists()
    assert not ClinicalWorkflowTransition.objects.exists()


def test_validation_requires_verified_identity_from_persisted_evidence() -> None:
    video = _awaiting_validation_video(verified=False)

    with pytest.raises(ClinicalLifecycleTransitionError) as rejected:
        _apply(video, E.ANONYMIZATION_VALIDATED, "validate-1")

    assert rejected.value.reason is TransitionRejection.IDENTITY_NOT_VERIFIED
    assert not ClinicalWorkflowTransition.objects.exists()


def test_verified_identity_allows_validation() -> None:
    video = _awaiting_validation_video(verified=True)

    result = _apply(video, E.ANONYMIZATION_VALIDATED, "validate-1")

    assert result.snapshot.state == ClinicalRecordState(
        PrivacyStage.VALIDATED, AnnotationStage.IN_PROGRESS, ReleaseStage.NOT_RELEASED
    )


def test_stale_version_loses_compare_and_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _pending_video()
    _apply(video, E.IMPORT_STARTED, "import-1")
    stale = ClinicalWorkflowRecord.objects.get(record_id=video.pk)
    stale.version = 0

    def stale_row(*_args: object) -> ClinicalWorkflowRecord:
        return stale

    monkeypatch.setattr(persistence, "_seeded_row", stale_row)

    with pytest.raises(WorkflowConcurrencyError):
        _apply(video, E.QUARANTINED, "quarantine-1")

    row = ClinicalWorkflowRecord.objects.get(record_id=video.pk)
    assert (row.privacy, row.version) == ("importing", 1)
    assert ClinicalWorkflowTransition.objects.count() == 1


@pytest.mark.parametrize("key", ["", "   ", "k" * 129])
def test_invalid_idempotency_key_is_rejected(key: str) -> None:
    video = _pending_video()

    with pytest.raises(ValueError, match="Idempotency key"):
        _apply(video, E.IMPORT_STARTED, key)


@pytest.mark.parametrize("column", ["privacy", "annotation", "release"])
def test_unknown_stored_token_fails_loudly(column: str) -> None:
    video = _pending_video()
    _apply(video, E.IMPORT_STARTED, "import-1")
    ClinicalWorkflowRecord.objects.filter(record_id=video.pk).update(
        **{column: "guessed"}
    )

    with pytest.raises(ValueError):
        read_workflow_state(VIDEO, video.pk, video_record_facts(video))


def test_transitions_are_append_only() -> None:
    video = _pending_video()
    _apply(video, E.IMPORT_STARTED, "import-1")
    transition = ClinicalWorkflowTransition.objects.get()

    with pytest.raises(ValueError, match="append-only"):
        transition.save()
    with pytest.raises(ValueError, match="append-only"):
        transition.delete()
