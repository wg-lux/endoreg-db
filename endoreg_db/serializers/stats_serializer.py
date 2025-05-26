from rest_framework import serializers

class StatsSerializer(serializers.Serializer):
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
    
    def get_completion_percentage(self, obj):
        """Calculate the percentage of completed videos."""
        if obj['totalVideos'] > 0:
            return round((obj['videosCompleted'] / obj['totalVideos']) * 100, 2)
        return 0