from rest_framework import serializers
from endoreg_db.models.upload_job import UploadJob


class UploadJobStatusSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for upload job status responses.
    Returns status information for polling endpoints.
    """
    
    sensitive_meta_id = serializers.IntegerField(
        source='sensitive_meta.id',
        read_only=True,
        allow_null=True,
        help_text="ID of the created SensitiveMeta record (only when anonymized)"
    )
    
    # Optional helper fields for preview (can be populated by view if needed)
    text = serializers.CharField(read_only=True, required=False, allow_blank=True)
    anonymized_text = serializers.CharField(read_only=True, required=False, allow_blank=True)

    class Meta:
        model = UploadJob
        fields = [
            'status',
            'error_detail', 
            'sensitive_meta_id',
            'id',
            'text',
            'anonymized_text'
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        """
        Customize the representation to only include relevant fields based on status.
        """
        data = super().to_representation(instance)
        
        # Only include error_detail if status is error
        if instance.status != UploadJob.Status.ERROR:
            data.pop('error_detail', None)
        
        # Only include sensitive_meta_id if status is anonymized and we have a meta record
        if instance.status != UploadJob.Status.ANONYMIZED or not instance.sensitive_meta:
            data.pop('sensitive_meta_id', None)
        
        # Remove empty optional fields
        if not data.get('text'):
            data.pop('text', None)
        if not data.get('anonymized_text'):
            data.pop('anonymized_text', None)
            
        return data


class UploadCreateResponseSerializer(serializers.Serializer):
    """
    Serializer for the initial upload response.
    Returns upload_id and status_url for polling.
    """
    
    upload_id = serializers.UUIDField(
        read_only=True,
        help_text="UUID of the created upload job"
    )
    
    status_url = serializers.CharField(
        read_only=True,
        help_text="URL to poll for upload status updates"
    )