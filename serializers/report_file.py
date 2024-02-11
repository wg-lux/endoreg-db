from rest_framework import serializers
from agl_base_db.models import ReportFile

class ReportFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportFile
        fields = '__all__'
