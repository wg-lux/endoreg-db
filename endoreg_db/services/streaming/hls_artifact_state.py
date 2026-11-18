"""Single writer of ``VideoHlsArtifact.status``.

The native ``decide_hls_artifact_transition`` table decides every status change;
see ``docs/video_hls_permissions_and_streaming.md``. Callers hold the artifact row lock
(``select_for_update``) or own an unsaved row, and save the status together
with their own fields.
"""

from __future__ import annotations

from django.db import transaction

from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.utils.rust_backend import decide_hls_artifact_transition


def set_hls_artifact_status(
    artifact: VideoHlsArtifact, target: VideoHlsArtifact.Status
) -> bool:
    """Assign ``target`` when the native table applies it; True means the caller saves.

    False means the artifact already reached or passed ``target`` (a stale or
    duplicate update), so the caller must not write. Illegal transitions raise
    ``ValueError``.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("HLS artifact status changes require an atomic block")
    current = "" if artifact.pk is None else str(artifact.status)
    if not decide_hls_artifact_transition(
        current_status=current, target_status=target.value
    ):
        return False
    artifact.status = target.value
    return True
