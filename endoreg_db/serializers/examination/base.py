# eendoreg_db/serializers/examination/base.py
from rest_framework import serializers
from ...models import Examination, ExaminationType


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
        Return a list of serialized findings associated with the given examination.
        
        Parameters:
            obj (Examination): The examination instance for which to retrieve findings.
        
        Returns:
            list: Serialized data for all findings available to the examination.
        """
        from ..finding import FindingSerializer
        findings = obj.get_available_findings()
        return FindingSerializer(findings, many=True).data

    def get_examination_types(self, obj:Examination):
        """
        Return a list of serialized examination types associated with the given examination.
        
        Parameters:
            obj (Examination): The examination instance for which to retrieve examination types.
        
        Returns:
            list: Serialized data for each related examination type.
        """
        return ExaminationTypeSerializer(obj.examination_types.all(), many=True).data
