# endoreg_db/serializers/video/video_file_list.py
from typing import Literal
import logging

from rest_framework import serializers

from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


class VideoFileListSerializer(serializers.ModelSerializer):
    """
    Minimal serializer to return only basic video information
    for the video selection dropdown in Vue.js.

    Convention:
        - Serializer methods must NOT raise if video state is missing or invalid.
        - They return safe defaults and log what went wrong.
    """

    # Add computed fields for video status
    status = serializers.SerializerMethodField()
    assignedUser = serializers.SerializerMethodField()
    anonymized = serializers.SerializerMethodField()

    class Meta:
        model = VideoFile
        fields = ["id", "original_file_name", "status", "assignedUser", "anonymized"]

    # --- internal helper -------------------------------------------------
    def _get_video_state(self, obj: VideoFile):
        """
        Best-effort accessor for obj.state.

        Serializer layer must never raise here; it only logs and returns None
        if the state cannot be loaded for any reason.
        """
        try:
            return getattr(obj, "state", None)
        except (
            Exception
        ) as exc:  # pragma: no cover - type of error is DB/backend-specific
            logger.warning(
                "VideoFileListSerializer: unable to access state for VideoFile(id=%s): %s",
                getattr(obj, "id", "unknown"),
                exc,
            )
            return None

    # --- public serializer fields ----------------------------------------
    def get_status(
        self, obj: VideoFile
    ) -> Literal["completed", "in_progress", "available"]:
        """
        Determine the processing status of a video file as 'completed',
        'in_progress', or 'available'.

        Contract:
            - Never raises.
            - Missing or invalid state -> treated as 'available'.
        """
        state = self._get_video_state(obj)

        if not state:
            return "available"

        # Use getattr with defaults to tolerate partially populated state objects
        anonymized = getattr(state, "anonymized", False)
        frames_extracted = getattr(state, "frames_extracted", False)

        if anonymized:
            return "completed"
        if frames_extracted:
            return "in_progress"
        return "available"

    def get_assignedUser(self, obj: VideoFile):
        """
        Returns the user assigned to the video, or None if no user is assigned.

        Currently always returns None as user assignment is not implemented.
        """
        # For now return None, can be extended when user assignment is implemented
        return None

    def get_anonymized(self, obj: VideoFile) -> bool:
        """
        Determine whether the video has been anonymized.

        Contract:
            - Never raises.
            - Returns False if state does not exist or cannot be loaded.
        """
        state = self._get_video_state(obj)
        if not state:
            return False

        # getattr to be robust against partially/populated state
        return bool(getattr(state, "anonymized", False))
