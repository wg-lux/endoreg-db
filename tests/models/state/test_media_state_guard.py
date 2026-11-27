from __future__ import annotations

from typing import TypeVar, cast

import pytest

from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.media.state import update_media_state
from endoreg_db.models.state.write_guard import GuardedStateManager

_State = TypeVar("_State", bound=VideoState | RawPdfState)


@pytest.mark.django_db
@pytest.mark.parametrize("state_type", [VideoState, RawPdfState])
def test_media_flags_reject_direct_create_and_save(state_type: type[_State]) -> None:
    with pytest.raises(ValueError):
        state_type.objects.create(anonymized=True)
    state = state_type.objects.create()
    state.anonymized = True
    with pytest.raises(ValueError):
        state.save(update_fields=["anonymized"])
    state.refresh_from_db()
    assert state.anonymized is False
    update_media_state(state, changes={"anonymized": True})
    state.refresh_from_db()
    assert state.anonymized is True


@pytest.mark.django_db
@pytest.mark.parametrize("state_type", [VideoState, RawPdfState])
def test_media_queryset_and_bulk_paths_cannot_write_flags(
    state_type: type[_State],
) -> None:
    manager = cast(GuardedStateManager[_State], state_type.objects)
    state = manager.create()
    with pytest.raises(ValueError):
        manager.filter(pk=state.pk).update(anonymized=True)
    state.anonymized = True
    with pytest.raises(ValueError):
        manager.bulk_update([state], ["anonymized"])
    with pytest.raises(ValueError):
        manager.bulk_create([state_type(anonymized=True)])
    with pytest.raises(ValueError):
        state_type._base_manager.filter(pk=state.pk).update(anonymized=True)
    state.refresh_from_db()
    assert state.anonymized is False


@pytest.mark.django_db
@pytest.mark.parametrize("state_type", [VideoState, RawPdfState])
def test_stale_and_deferred_media_instances_cannot_overwrite_authoritative_flags(
    state_type: type[_State],
) -> None:
    state = state_type.objects.create()
    stale = state_type.objects.get(pk=state.pk)
    deferred = state_type.objects.only("pk").get(pk=state.pk)
    update_media_state(state, changes={"anonymized": True})
    with pytest.raises(ValueError):
        stale.save()
    deferred.anonymized = False
    with pytest.raises(ValueError):
        deferred.save(update_fields=["anonymized"])
    with pytest.raises(ValueError, match="changed since"):
        update_media_state(stale, changes={"anonymized": False})
    # An explicit service operation preserves unrelated newer evidence.
    update_media_state(stale, changes={"text_meta_extracted": True})
    state.refresh_from_db()
    assert state.anonymized is True
    assert state.text_meta_extracted is True


@pytest.mark.django_db
@pytest.mark.parametrize("state_type", [VideoState, RawPdfState])
def test_save_false_is_not_a_reusable_write_capability(
    state_type: type[_State],
) -> None:
    state = state_type.objects.create()
    state.mark_anonymized(save=False)
    assert state.anonymized is True
    with pytest.raises(ValueError):
        state.save()
    state.refresh_from_db()
    assert state.anonymized is False
    state.mark_anonymized()
    state.refresh_from_db()
    assert state.anonymized is True


@pytest.mark.django_db
def test_video_transition_rechecks_failure_under_lock() -> None:
    state = VideoState.objects.create()
    stale = VideoState.objects.get(pk=state.pk)
    state.mark_processing_failed()
    with pytest.raises(ValueError, match="failed/lost"):
        stale.mark_anonymized()
    state.refresh_from_db()
    assert state.processing_error is True
    assert state.anonymized is False


@pytest.mark.django_db
def test_export_promotion_rechecks_current_review_evidence() -> None:
    state = VideoState.objects.create()
    update_media_state(
        state,
        changes={
            "anonymization_validated": True,
            "outside_segments_removed": True,
            "segment_annotations_validated": True,
        },
    )
    stale = VideoState.objects.get(pk=state.pk)
    update_media_state(state, changes={"anonymization_validated": False})
    with pytest.raises(ValueError, match="anonymization has not been validated"):
        stale.mark_ready_for_export(
            processed_file_sha256="a" * 64, ready_for_export_by="reviewer"
        )
    state.refresh_from_db()
    assert state.ready_for_export is False


@pytest.mark.django_db
@pytest.mark.parametrize("state_type", [VideoState, RawPdfState])
def test_explicit_service_can_create_state_without_allowing_unrelated_dirty_fields(
    state_type: type[_State],
) -> None:
    state = state_type()
    update_media_state(state, changes={"anonymized": True})
    assert state.pk is not None
    state.refresh_from_db()
    assert state.anonymized is True
    with pytest.raises(ValueError):
        update_media_state(
            state_type(sensitive_meta_processed=True), changes={"anonymized": True}
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_media_updates_cannot_both_spend_same_snapshot() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connections
    from endoreg_db.models.state.write_guard import StateWriteForbidden

    state = VideoState.objects.create()
    barrier = Barrier(2)

    def mutate() -> bool:
        try:
            current = VideoState.objects.get(pk=state.pk)
            barrier.wait(timeout=10)
            try:
                update_media_state(current, changes={"anonymized": True})
            except StateWriteForbidden:
                return False
            return True
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(mutate) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == [False, True]
    state.refresh_from_db()
    assert state.anonymized is True
