from __future__ import annotations

import pytest

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.video_files.state import get_or_create_video_state


@pytest.fixture
def center(db: None) -> Center:
    return Center.objects.create(name="State Identity Center")


@pytest.mark.django_db
def test_stale_video_instance_reuses_persisted_state_and_review_flags(
    center: Center,
) -> None:
    video = VideoFile.objects.create(center=center, video_hash="stale-state-identity")
    stale = VideoFile.objects.get(pk=video.pk)
    assert stale.state is None
    first = get_or_create_video_state(video)
    first.anonymization_validated = True
    first.save(update_fields=["anonymization_validated"])

    second = get_or_create_video_state(stale)

    assert second.pk == first.pk
    assert second.anonymization_validated is True
    assert VideoState.objects.count() == 1
    video.refresh_from_db()
    assert video.state is not None
    assert video.state.pk == first.pk


@pytest.mark.django_db
def test_stale_cached_state_cannot_replace_current_authoritative_state(
    center: Center,
) -> None:
    video = VideoFile.objects.create(
        center=center, video_hash="replaced-state-identity"
    )
    first = get_or_create_video_state(video)
    replacement = VideoState.objects.create(anonymization_validated=True)
    VideoFile.objects.filter(pk=video.pk).update(state=replacement)

    current = get_or_create_video_state(video)

    assert current.pk == replacement.pk
    assert current.pk != first.pk
    assert current.anonymization_validated is True
    assert VideoState.objects.count() == 2
