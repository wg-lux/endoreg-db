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
        Determine the processing status of a video file as 'completed', 'in_progress', or 'available'.
        
        Returns:
            str: The video's status based on its processing state.
            
        Raises:
            ValueError: If the video's state cannot be accessed.
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
        Returns the user assigned to the video, or None if no user is assigned.
        
        Currently always returns None as user assignment is not implemented.
        """
        # For now return None, can be extended when user assignment is implemented
        return None

    def get_anonymized(self, obj):
        """
        Determine whether the video has been anonymized.
        
        Returns:
            bool: True if the video's state indicates it is anonymized; False otherwise or if state information is unavailable.
        """
        try:
            state = getattr(obj, 'state', None)
            if state:
                return getattr(state, 'anonymized', False)
            return False
        except Exception:
            return False