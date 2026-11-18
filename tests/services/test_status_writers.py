"""Native status tables decide every lifecycle write from the persisted status."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.db import transaction

from endoreg_db.models import FrameAnnotationExportJob, QuarantineItem
from endoreg_db.services.runtime.status_writers import (
    StatusMachine,
    set_quarantine_item_status,
    update_frame_export_job_status,
)
from endoreg_db.utils.rust_backend import decide_status_transition

NOW = datetime(2026, 11, 18, tzinfo=UTC)


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("machine", "current", "target", "applies"),
    [
        (StatusMachine.REPORT_LLM_INFERENCE_JOB, "failure", "running", True),
        (StatusMachine.REPORT_LLM_INFERENCE_JOB, "success", "lost", False),
        (StatusMachine.VIDEO_PROCESSING_HISTORY, "running", "cancelled", True),
        (StatusMachine.QUARANTINE_ITEM, "approved_for_deletion", "deleted", True),
        (StatusMachine.FRAME_ANNOTATION_EXPORT_JOB, "queued", "running", True),
        (StatusMachine.AI_DATASET_EXPORT_ARTIFACT, "completed", "failed", True),
        (StatusMachine.DICOM_EXPORT_JOB, "imported", "imported", False),
        (StatusMachine.PATIENT_EXAMINATION_REPORT, "final", "draft", True),
        (StatusMachine.HUB_INBOUND_TRANSFER, "applied", "lost", True),
        (StatusMachine.HUB_INBOUND_CASE_RESOLUTION, "", "pending", True),
        (StatusMachine.UPLOAD_JOB_SOURCE_CLEANUP, "completed", "skipped", False),
        (StatusMachine.HUB_OUTBOUND_LOCAL_CLEANUP, "eligible", "cleaning", True),
    ],
)
def test_native_tables_apply_or_ignore(
    machine: StatusMachine, current: str, target: str, applies: bool
) -> None:
    assert (
        decide_status_transition(machine=machine.value, current=current, target=target)
        is applies
    )


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("machine", "current", "target"),
    [
        ("report_llm_inference_job", "cancelled", "running"),
        ("quarantine_item", "retained", "deleted"),
        ("frame_annotation_export_job", "queued", "completed"),
        ("hub_inbound_transfer", "inconsistent", "running"),
        ("upload_job_source_cleanup", "deleting", "skipped"),
        ("hub_outbound_local_cleanup", "retained", "cleaning"),
        ("quarantine_item", "unknown", "deleted"),
        ("unknown_machine", "", "queued"),
    ],
)
def test_illegal_or_unknown_transitions_raise(
    machine: str, current: str, target: str
) -> None:
    with pytest.raises(ValueError):
        decide_status_transition(machine=machine, current=current, target=target)


@pytest.mark.no_db
def test_writers_require_an_atomic_block() -> None:
    item = QuarantineItem(path="/q/a", relative_path="a")
    with pytest.raises(RuntimeError, match="atomic"):
        set_quarantine_item_status(item, QuarantineItem.Status.RETAINED.value)


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("expected", "target", "fields"),
    [
        ("completed", "running", {}),
        ("running", "running", {}),
        ("queued", "running", {"status": "lost"}),
    ],
)
def test_compare_and_set_rejects_non_transitions_and_lifecycle_fields(
    expected: str, target: str, fields: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        update_frame_export_job_status(
            FrameAnnotationExportJob.objects.none(),
            expected=expected,
            target=target,
            **fields,
        )


def _item(status: str) -> QuarantineItem:
    return QuarantineItem.objects.create(
        path=f"/quarantine/{status}",
        relative_path=status,
        quarantined_at=NOW,
        last_seen_at=NOW,
        status=status,
    )


@pytest.mark.django_db
def test_persisted_status_decides_not_the_stale_instance() -> None:
    item = _item(QuarantineItem.Status.APPROVED_FOR_DELETION.value)
    stale = QuarantineItem.objects.get(pk=item.pk)
    with transaction.atomic():
        assert set_quarantine_item_status(item, QuarantineItem.Status.DELETED.value)
        item.deleted_at = NOW
        item.save(update_fields=["status", "deleted_at"])
    # The stale copy still says approved, but the row is deleted: reject.
    with pytest.raises(ValueError, match="deleted -> retained"):
        with transaction.atomic():
            set_quarantine_item_status(stale, QuarantineItem.Status.RETAINED.value)


@pytest.mark.django_db
def test_ignored_transition_resyncs_the_instance() -> None:
    item = _item(QuarantineItem.Status.MISSING.value)
    stale = QuarantineItem.objects.get(pk=item.pk)
    stale.status = QuarantineItem.Status.PENDING_REVIEW.value
    with transaction.atomic():
        assert not set_quarantine_item_status(
            stale, QuarantineItem.Status.MISSING.value
        )
    assert stale.status == QuarantineItem.Status.MISSING.value
