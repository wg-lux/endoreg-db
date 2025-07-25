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
        return self.get_localized_name(obj.intervention)