from endoreg_db.models import Finding, PatientFindingClassification
from endoreg_db.serializers.misc.translatable_field_mix_in import TranslatableFieldMixin


from rest_framework import serializers


class PatientFindingClassificationSerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Serializer für PatientFinding-Klassifikationen"""

    classification_name = serializers.SerializerMethodField()
    classification_choice_name = serializers.SerializerMethodField()
    class Meta:
        model = PatientFindingClassification
        fields = ['id', 'classification', 'classification_name', 'classification_choice_name']

    def get_classification_name(self, obj:PatientFindingClassification):
        return self.get_localized_name(obj.classification)

    def get_classification_choice_name(self, obj:PatientFindingClassification):
        return self.get_localized_name(obj.classification_choice)