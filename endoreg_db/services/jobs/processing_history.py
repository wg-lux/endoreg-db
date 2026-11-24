"""Shared task identity persistence for video processing histories."""

from endoreg_db.models.media.video.video_processing import VideoProcessingHistory


def set_history_task_id(history: VideoProcessingHistory, task_id: str) -> None:
    """Retain the current history without another write for an unchanged task."""
    if history.task_id == task_id:
        return
    history.task_id = task_id
    history.save(update_fields=["task_id"])
