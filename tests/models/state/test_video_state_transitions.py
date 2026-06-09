from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from endoreg_db.models.state.video import SHA256_HEX_LENGTH, VideoState


VALID_SHA256 = "a" * SHA256_HEX_LENGTH


def _assert_rejected_by_database(**state_values: object) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        VideoState.objects.create(**state_values)


def _ready_state_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "ready_for_export": True,
        "ready_for_export_at": timezone.now(),
        "ready_for_export_by": "validator",
        "processed_file_sha256": VALID_SHA256,
        "processing_error": False,
        "anonymization_validated": True,
        "outside_segments_removed": True,
        "segment_annotations_validated": True,
    }
    values.update(overrides)
    return values


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state_values",
    [
        {"processing_error": True, "processing_started": True},
        _ready_state_values(processing_error=True),
        _ready_state_values(anonymization_validated=False),
        _ready_state_values(outside_segments_removed=False),
        _ready_state_values(segment_annotations_validated=False),
        _ready_state_values(ready_for_export_at=None),
        _ready_state_values(ready_for_export_by=""),
        _ready_state_values(processed_file_sha256=""),
    ],
)
def test_database_rejects_unsafe_video_state_combinations(state_values):
    _assert_rejected_by_database(**state_values)


@pytest.mark.django_db
def test_database_accepts_ready_state_with_required_evidence():
    state = VideoState.objects.create(**_ready_state_values())

    assert state.ready_for_export is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("state_values", "expected_error"),
    [
        ({}, "anonymization has not been validated"),
        ({"anonymization_validated": True}, "outside segments have not been removed"),
        (
            {"anonymization_validated": True, "outside_segments_removed": True},
            "segment annotations have not been validated",
        ),
    ],
)
def test_mark_ready_for_export_enforces_transition_order(
    state_values,
    expected_error,
):
    state = VideoState.objects.create(**state_values)

    with pytest.raises(ValueError, match=expected_error):
        state.mark_ready_for_export(
            processed_file_sha256=VALID_SHA256,
            ready_for_export_by="validator",
        )

    state.refresh_from_db()
    assert state.ready_for_export is False
    assert state.ready_for_export_at is None
    assert state.ready_for_export_by == ""
    assert state.processed_file_sha256 == ""


@pytest.mark.django_db
def test_processing_error_remains_terminal_and_clears_in_progress_state():
    state = VideoState.objects.create(
        anonymization_validated=True,
        outside_segments_removed=True,
        segment_annotations_validated=True,
    )
    state.mark_processing_started()

    state.mark_processing_failed()

    state.refresh_from_db()
    assert state.processing_error is True
    assert state.processing_started is False
    assert state.ready_for_export is False
    with pytest.raises(ValueError, match="failed/lost"):
        state.mark_frames_extracted()
    with pytest.raises(ValueError, match="failed/lost"):
        state.mark_ready_for_export(
            processed_file_sha256=VALID_SHA256,
            ready_for_export_by="validator",
        )


@pytest.mark.django_db
def test_mark_ready_for_export_normalizes_required_evidence():
    state = VideoState.objects.create(
        anonymization_validated=True,
        outside_segments_removed=True,
        segment_annotations_validated=True,
    )

    state.mark_ready_for_export(
        processed_file_sha256=VALID_SHA256.upper(),
        ready_for_export_by=" validator ",
    )

    state.refresh_from_db()
    assert state.ready_for_export is True
    assert state.processed_file_sha256 == VALID_SHA256
    assert state.ready_for_export_by == "validator"
    assert state.ready_for_export_at is not None
