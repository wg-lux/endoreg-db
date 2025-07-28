from endoreg_db.serializers.misc.translatable_field_mix_in import TranslatableFieldMixin
from endoreg_db.models import PatientFindingIntervention

from rest_framework import serializers


class PatientFindingInterventionSerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Optimierter Serializer für PatientFindingIntervention"""

    intervention_name = serializers.SerializerMethodField()

    class Meta:
        model = PatientFindingIntervention
        fields = ['id', 'intervention', 'intervention_name', 'state']

    def get_intervention_name(self, obj):
        """
        Return the localized name of the intervention associated with the given object.
        
        Parameters:
            obj: The object containing the intervention to be localized.
        
        Returns:
            str: The localized name of the intervention.
        """
        return self.get_localized_name(obj.intervention)