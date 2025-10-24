"""
Video Metadata Serializer

Serializes VideoMetadata model for API responses.
Created as part of Phase 1.1: Video Correction API Endpoints.
"""
from rest_framework import serializers
from endoreg_db.models.media.video import VideoMetadata
import json


class VideoMetadataSerializer(serializers.ModelSerializer):
    """
    Serializer for VideoMetadata model.
    
    Provides analysis results (sensitive frame count, ratio, frame IDs)
    for the correction UI.
    """
    sensitive_frame_ids_list = serializers.SerializerMethodField()
    sensitive_percentage = serializers.ReadOnlyField()
    has_analysis = serializers.ReadOnlyField()
    
    class Meta:
        model = VideoMetadata
        fields = [
            'id',
            'video',
            'sensitive_frame_count',
            'sensitive_ratio',
            'sensitive_frame_ids',
            'sensitive_frame_ids_list',
            'sensitive_percentage',
            'has_analysis',
            'analyzed_at',
        ]
        read_only_fields = ['id', 'analyzed_at']
    
    def get_sensitive_frame_ids_list(self, obj) -> list:
        """
        Parse sensitive_frame_ids from JSON string to Python list.
        
        Returns:
            list: Frame indices (0-based), or empty list if no analysis
        """
        if not obj.sensitive_frame_ids:
            return []
        
        try:
            return json.loads(obj.sensitive_frame_ids)
        except (json.JSONDecodeError, TypeError):
            return []
    
    def validate_sensitive_frame_ids(self, value):
        """
        Validate that sensitive_frame_ids is a valid JSON array.
        
        Args:
            value: JSON string representing frame IDs
            
        Returns:
            str: Validated JSON string
            
        Raises:
            ValidationError: If value is not valid JSON or not an array
        """
        if not value:
            return value
        
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, list):
                raise serializers.ValidationError(
                    "sensitive_frame_ids must be a JSON array"
                )
            
            # Validate all elements are integers
            if not all(isinstance(x, int) for x in parsed):
                raise serializers.ValidationError(
                    "All frame IDs must be integers"
                )
            
            return value
        except json.JSONDecodeError:
            raise serializers.ValidationError(
                "sensitive_frame_ids must be valid JSON"
            )
    
    def validate_sensitive_ratio(self, value):
        """
        Validate that sensitive_ratio is between 0.0 and 1.0.
        
        Args:
            value: Ratio value
            
        Returns:
            float: Validated ratio
            
        Raises:
            ValidationError: If value is outside valid range
        """
        if value is not None and (value < 0.0 or value > 1.0):
            raise serializers.ValidationError(
                "sensitive_ratio must be between 0.0 and 1.0"
            )
        return value
