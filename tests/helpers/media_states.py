"""Construct media-state fixtures through their owning transition service."""

from django.db import transaction

from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.media.state import update_media_state


@transaction.atomic
def create_media_state[M: VideoState | RawPdfState](
    model: type[M], **changes: object
) -> M:
    state = model()
    state.save()
    update_media_state(state, changes=changes)
    return state
