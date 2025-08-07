from endoreg_db.models import PatientFindingClassification
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
        """
        Return the localized name for the classification attribute of a PatientFindingClassification instance.
        
        Parameters:
            obj (PatientFindingClassification): The instance whose classification name is to be localized.
        
        Returns:
            str: The localized classification name.
        """
        return self.get_localized_name(obj.classification)

    def get_classification_choice_name(self, obj:PatientFindingClassification):
        """
        Return the localized name for the classification choice of a patient finding classification instance.
        
        Parameters:
            obj (PatientFindingClassification): The patient finding classification instance.
        
        Returns:
            str: Localized name of the classification choice.
        """
        return self.get_localized_name(obj.classification_choice)