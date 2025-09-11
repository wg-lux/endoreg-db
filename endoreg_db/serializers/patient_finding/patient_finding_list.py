from endoreg_db.models import PatientFinding
from endoreg_db.serializers.misc.translatable_field_mix_in import TranslatableFieldMixin


from rest_framework import serializers


class PatientFindingListSerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Leichtgewichtiger Serializer für Listen-Ansichten"""

    finding_name = serializers.SerializerMethodField()
    patient_name = serializers.CharField(
        source='patient_examination.patient.get_full_name',
        read_only=True
    )
    examination_date = serializers.DateTimeField(
        source='patient_examination.date_start',
        read_only=True
    )

    class Meta:
        model = PatientFinding
        fields = [
            'id', 'finding_name', 'patient_name', 'examination_date',
            'patient_examination', 'finding'
        ]

    def get_finding_name(self, obj):
        """
        Return the localized name of the finding associated with the given PatientFinding instance.
        
        Parameters:
            obj: The PatientFinding instance for which to retrieve the localized finding name.
        
        Returns:
            str: The localized name of the related finding.
        """
        return self.get_localized_name(obj.finding)
    

 