"""
Video Processing History Serializer

Serializes VideoProcessingHistory model for API responses.
Created as part of Phase 1.1: Video Correction API Endpoints.
"""
from collections.abc import Mapping

from rest_framework import serializers

from endoreg_db.models import VideoProcessingHistory


class VideoProcessingHistorySerializer(serializers.ModelSerializer):
    """
    Serializer for VideoProcessingHistory model.
    
    Provides operation audit trail (masking, frame removal, analysis)
    with download URLs for processed files.
    """
    download_url = serializers.SerializerMethodField()
    operation_display = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    duration = serializers.ReadOnlyField()
    is_complete = serializers.ReadOnlyField()
    
    class Meta:
        model = VideoProcessingHistory
        fields = [
            'id',
            'video',
            'operation',
            'operation_display',
            'status',
            'status_display',
            'config',
            'output_file',
            'download_url',
            'details',
            'task_id',
            'created_at',
            'completed_at',
            'duration',
            'is_complete',
        ]
        read_only_fields = ['id', 'created_at', 'completed_at']
    
    def get_download_url(self, obj) -> str | None:
        """
        Generate download URL for processed video file.
        
        Args:
            obj: VideoProcessingHistory instance
            
        Returns:
            str: URL to download processed file, or None if not available
        """
        if not obj.output_file or obj.status != VideoProcessingHistory.STATUS_SUCCESS:
            return None
        
        # Build URL to download endpoint (to be implemented)
        # Format: /api/media/processed-videos/{video_id}/{history_id}/
        context = self.context if isinstance(self.context, Mapping) else None
        request = context.get('request') if context else None
        if request:
            return request.build_absolute_uri(
                f'/api/media/processed-videos/{obj.video.id}/{obj.id}/'
            )
        
        return f'/api/media/processed-videos/{obj.video.id}/{obj.id}/'

    def get_operation_display(self, obj) -> str:
        display = getattr(obj, "get_operation_display", None)
        result = display() if callable(display) else obj.operation
        return str(result)

    def get_status_display(self, obj) -> str:
        display = getattr(obj, "get_status_display", None)
        result = display() if callable(display) else obj.status
        return str(result)
    
    def validate_operation(self, value):
        """
        Validate operation is one of the defined choices.
        
        Args:
            value: Operation type
            
        Returns:
            str: Validated operation
            
        Raises:
            ValidationError: If operation is invalid
        """
        valid_operations = [choice[0] for choice in VideoProcessingHistory.OPERATION_CHOICES]
        if value not in valid_operations:
            raise serializers.ValidationError(
                f"Invalid operation. Must be one of: {', '.join(valid_operations)}"
            )
        return value
    
    def validate_status(self, value):
        """
        Validate status is one of the defined choices.
        
        Args:
            value: Status type
            
        Returns:
            str: Validated status
            
        Raises:
            ValidationError: If status is invalid
        """
        valid_statuses = [choice[0] for choice in VideoProcessingHistory.STATUS_CHOICES]
        if value not in valid_statuses:
            raise serializers.ValidationError(
                f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )
        return value
    
    def validate_config(self, value):
        """
        Validate config based on operation type.
        
        Args:
            value: Config dictionary
            
        Returns:
            dict: Validated config
            
        Raises:
            ValidationError: If config is invalid for operation
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be a dictionary")

        initial = self.initial_data if isinstance(self.initial_data, Mapping) else {}
        operation = initial.get('operation')
        
        # Validate masking config
        if operation == VideoProcessingHistory.OPERATION_MASKING:
            required_fields = ['mask_type']
            if 'mask_type' not in value:
                raise serializers.ValidationError(
                    f"Masking config must include: {', '.join(required_fields)}"
                )
            
            # If device mask, require device_name
            if value['mask_type'] == 'device' and 'device_name' not in value:
                raise serializers.ValidationError(
                    "Device mask requires 'device_name' in config"
                )
            
            # If custom ROI, require roi coordinates
            if value['mask_type'] == 'custom' and 'roi' not in value:
                raise serializers.ValidationError(
                    "Custom mask requires 'roi' coordinates in config"
                )
        
        # Validate frame removal config
        elif operation == VideoProcessingHistory.OPERATION_FRAME_REMOVAL:
            if 'frame_list' not in value and 'detection_method' not in value:
                raise serializers.ValidationError(
                    "Frame removal config must include 'frame_list' (manual) or 'detection_method' (automatic)"
                )
        
        return value
