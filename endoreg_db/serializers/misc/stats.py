from rest_framework import serializers
from typing import TypedDict


class StatsInputPayload(TypedDict):
    total_videos: int
    videos_completed: int


class StatsSerializer(serializers.Serializer[StatsInputPayload]):
    """
    Serializer for statistics data from AuditLedger.

    This serializer defines the structure of statistics data and ensures
    consistent API responses.
    """

    totalCases = serializers.IntegerField()
    totalVideos = serializers.IntegerField()
    totalAnnotations = serializers.IntegerField()
    totalAnonymizations = serializers.IntegerField()
    totalImages = serializers.IntegerField()
    videosCompleted = serializers.IntegerField()
    videosAnonym = serializers.IntegerField()

    # You can add additional fields or methods here as needed
    # For example, to calculate derived statistics:
    # TODO
    def get_completion_percentage(self, obj: StatsInputPayload) -> float:
        """
        Calculates the percentage of completed videos out of the total videos.

        Args:
            obj: A dictionary containing 'videos_completed' and 'total_videos' keys.

        Returns:
            The completion percentage as a float rounded to two decimal places, or 0 if totalVideos is zero or less.
        """
        if obj["total_videos"] > 0:
            return round((obj["videos_completed"] / obj["total_videos"]) * 100, 2)
        return 0.0
