from endoreg_db.models import RawPdfFile
from endoreg_db.serializers.meta.report_meta import ReportMetaSerializer


from django.utils import timezone
from rest_framework import serializers


from pathlib import Path


class ReportListSerializer(serializers.ModelSerializer):
    """
    Vereinfachter Serializer für Report-Listen
    """
    report_meta = ReportMetaSerializer(source='sensitive_meta', read_only=True)
    file_type = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = RawPdfFile
        fields = ['id', 'status', 'report_meta', 'file_type', 'created_at', 'updated_at']

    def get_status(self, obj):
        """Ermittelt den Status basierend auf Verarbeitungsstatus"""
        if hasattr(obj, 'state_report_processed') and obj.state_report_processed:
            return 'approved'
        elif hasattr(obj, 'state_report_processing_required') and obj.state_report_processing_required:
            return 'pending'
        else:
            return 'pending'

    def get_updated_at(self, obj):
        """Simuliert updated_at basierend auf created_at"""
        return obj.created_at if obj.created_at else timezone.now()

    def get_file_type(self, obj):
        if obj.file:
            return Path(obj.file.name).suffix.lower().lstrip('.')
        return 'unknown'