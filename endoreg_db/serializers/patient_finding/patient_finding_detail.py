from rest_framework import serializers
from endoreg_db.models import (
    PatientFinding,
)
from endoreg_db.serializers.misc.translatable_field_mix_in import TranslatableFieldMixin
from endoreg_db.serializers.patient_finding.patient_finding_intervention import PatientFindingInterventionSerializer
from .patient_finding_classification import PatientFindingClassificationSerializer

class PatientFindingDetailSerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Vollständiger Serializer mit allen verschachtelten Daten - optimiert für N+1 Vermeidung"""
    
    
    classifications = PatientFindingClassificationSerializer(many=True, read_only=True)
    interventions = PatientFindingInterventionSerializer(many=True, read_only=True)
    
    # Meta-Informationen für das Frontend
    patient_id = serializers.CharField(source='patient_examination.patient.id', read_only=True)
    examination_name = serializers.SerializerMethodField()
    finding_name = serializers.SerializerMethodField()

    class Meta:
        model = PatientFinding
        fields = [
            'id', 'patient_examination', 'finding', 'finding_name',
            'patient_id', 'examination_name',
            'classifications', 'interventions'
        ]
        
    def get_finding_name(self, obj):
        """
        Return the localized name of the finding associated with the given PatientFinding instance.
        
        Parameters:
            obj: The PatientFinding instance being serialized.
        
        Returns:
            str: The localized name of the related finding.
        """
        return self.get_localized_name(obj.finding)
    
    def get_examination_name(self, obj):
        """
        Return the localized name of the examination associated with the given PatientFinding instance.
        
        Parameters:
            obj: The PatientFinding instance being serialized.
        
        Returns:
            str: The localized name of the related examination.
        """
        return self.get_localized_name(obj.patient_examination.examination)


