from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile
    from endoreg_db.models.state.video import VideoState


def get_or_create_video_state(video: "VideoFile") -> "VideoState":
    """Ensure a VideoFile has a persisted VideoState and return it."""
    from endoreg_db.models.state.video import VideoState

    state = video.state
    state_pk = getattr(state, "pk", None)
    if state is not None and state_pk is not None:
        if not VideoState.objects.filter(pk=state_pk).exists():
            state = None

    if state is None:
        state = VideoState.objects.create()
        video.state = state
        if video.pk:
            video.save(update_fields=["state"])

    return state
