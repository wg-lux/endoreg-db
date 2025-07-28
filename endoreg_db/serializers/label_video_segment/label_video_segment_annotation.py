from rest_framework import serializers
import logging

logger = logging.getLogger(__name__)


class LabelVideoSegmentAnnotationSerializer(serializers.Serializer):
    """
    Serializer for annotation data.
    Handles segment annotations with metadata including segmentId.
    """
    id = serializers.IntegerField(read_only=True)
    videoId = serializers.IntegerField(required=True)
    startTime = serializers.FloatField(required=False)
    endTime = serializers.FloatField(required=False)
    type = serializers.CharField(max_length=50, required=True)
    text = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    tags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        default=list
    )
    userId = serializers.CharField(max_length=100, required=False)
    isPublic = serializers.BooleanField(default=False)
    confidence = serializers.FloatField(required=False, min_value=0.0, max_value=1.0)
    metadata = serializers.DictField(required=False, default=dict)
    
    def validate(self, data):
        """
        Validate annotation data.
        For segment annotations, ensure startTime and endTime are provided.
        """
        annotation_type = data.get('type')
        
        if annotation_type == 'segment':
            if 'startTime' not in data or 'endTime' not in data:
                raise serializers.ValidationError(
                    "Segment annotations must include startTime and endTime"
                )
            
            start_time = data.get('startTime')
            end_time = data.get('endTime')
            
            if start_time >= end_time:
                raise serializers.ValidationError(
                    "startTime must be less than endTime"
                )
                
            # Validate metadata contains segmentId if provided
            metadata = data.get('metadata', {})
            segment_id = metadata.get('segmentId')
            if segment_id is not None:
                try:
                    int(segment_id)
                except (ValueError, TypeError):
                    raise serializers.ValidationError(
                        "metadata.segmentId must be a valid integer"
                    )
        
        return data
    
    #@maxhild we need to verify if this is the right place for these methods, if so we should implement them here
    def create(self, validated_data):
        """
        Create annotation data structure.
        In a real implementation, this would save to a database or storage system.
        """
        # Simulate creating an annotation with an ID
        validated_data['id'] = getattr(self, '_next_id', 1)
        self._next_id = validated_data['id'] + 1
        return validated_data
    
    def update(self, instance, validated_data):
        """
        Update annotation data structure.
        """
        for attr, value in validated_data.items():
            instance[attr] = value
        return instance
    
    def save(self, **kwargs):
        """
        Save the annotation.
        """
        if self.instance:
            return self.update(self.instance, self.validated_data)
        else:
            return self.create(self.validated_data)