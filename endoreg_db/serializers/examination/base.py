# endoreg_db/serializers.py
from rest_framework import serializers
from ...models import Examination, ExaminationType, Finding


class ExaminationTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExaminationType
        fields = ['id', 'name']
class ExaminationSerializer(serializers.ModelSerializer):
    

    findings = serializers.SerializerMethodField()
    examination_types = serializers.SerializerMethodField()
    class Meta:
        model = Examination
        fields = [
            'id', 'name','findings', 'examination_types'
        ]


    def get_findings(self, obj:Examination):
        """
        Retrieves all findings associated with the examination.
        Returns:
            list: A list of serialized findings related to the examination.
        """
        from ..finding import FindingSerializer
        findings = obj.get_available_findings()
        return FindingSerializer(findings, many=True).data

    def get_examination_types(self, obj:Examination):
        """
        Retrieves all examination types associated with the examination.
        Returns:
            list: A list of serialized examination types related to the examination.
        """
        return ExaminationTypeSerializer(obj.examination_types.all(), many=True).data
