from typing import Literal
from endoreg_db.models import VideoFile


from rest_framework import serializers


class VideoFileListSerializer(serializers.ModelSerializer):
    """
    Minimal serializer to return only basic video information
    for the video selection dropdown in Vue.js.
    """

    # Add computed fields for video status
    status = serializers.SerializerMethodField()
    assignedUser = serializers.SerializerMethodField()
    anonymized = serializers.SerializerMethodField()

    class Meta:
        model = VideoFile
        fields = ["id", "original_file_name", "status", "assignedUser", "anonymized"]

    def get_status(self, obj:VideoFile) -> Literal['completed'] | Literal['in_progress'] | Literal['available']:
        """
        Returns the processing status of a video as 'completed', 'in_progress', or 'available'.
        """
        try:
            state = obj.state
            if not state:
                return 'available'
            if state.anonymized:
                return 'completed'
            if state.frames_extracted:
                return 'in_progress'
            return 'available'
        except Exception as _e:
            raise ValueError("Video state should not be None") from _e

    def get_assignedUser(self, obj):
        """
        Get assigned user for the video
        """
        # For now return None, can be extended when user assignment is implemented
        return None

    def get_anonymized(self, obj):
        """
        Check if video is anonymized - returns boolean instead of status string
        """
        try:
            state = getattr(obj, 'state', None)
            if state:
                return getattr(state, 'anonymized', False)
            return False
        except Exception:
            return False