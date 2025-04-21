from rest_framework import serializers
from endoreg_db.models import AnonymExaminationReport

class AnonymExaminationReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnonymExaminationReport
        fields = '__all__'
