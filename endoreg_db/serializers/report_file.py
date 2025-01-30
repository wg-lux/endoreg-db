from rest_framework import serializers
from endoreg_db.models import ReportFile

class ReportFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportFile
        fields = '__all__'
