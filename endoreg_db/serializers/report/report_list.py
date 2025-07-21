from endoreg_db.models import RawPdfFile
from endoreg_db.serializers.meta import ReportMetaSerializer
from endoreg_db.serializers.report.mixins import ReportStatusMixin

from django.utils import timezone
from rest_framework import serializers


from pathlib import Path


class ReportListSerializer(ReportStatusMixin, serializers.ModelSerializer):
    """
    Vereinfachter Serializer für Report-Listen
    """
    report_meta = ReportMetaSerializer(source='sensitive_meta', read_only=True)
    file_type = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = RawPdfFile
        fields = ['id', 'status', 'report_meta', 'file_type', 'created_at', 'updated_at']