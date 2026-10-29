from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.state.video import VideoState


def get_or_create_video_state(video: "VideoFile") -> "VideoState":
    """Ensure a VideoFile has a persisted VideoState and return it."""
    from endoreg_db.models.state.video import VideoState
    from endoreg_db.models.media.video.video_file import VideoFile

    if video.pk:
        with transaction.atomic():
            # Read the relation after acquiring the lock. A joined relation can
            # retain the snapshot from before a competing creator committed.
            current_video = VideoFile.objects.select_for_update(of=("self",)).get(
                pk=video.pk
            )
            state = current_video.state
            if state is None:
                state = VideoState.objects.create()
                current_video.state = state
                current_video.save(update_fields=["state"])
            video.state = state
            return state

    state = video.state
    state_pk = getattr(state, "pk", None)
    if state is not None and state_pk is not None:
        if not VideoState.objects.filter(pk=state_pk).exists():
            state = None

    if state is None:
        state = VideoState.objects.create()
        video.state = state

    return state
