from endoreg_db.models import (
    PatientFinding,
    PatientFindingIntervention
)
from endoreg_db.serializers.patient_finding.patient_finding_classification import PatientFindingClassificationSerializer
from endoreg_db.serializers.patient_finding.patient_finding_intervention import PatientFindingInterventionSerializer


from rest_framework import serializers


class PatientFindingWriteSerializer(serializers.ModelSerializer):
    """Schreiboptimierter Serializer mit Nested Write Support"""

    classifications = PatientFindingClassificationSerializer(many=True, required=False)
    interventions = PatientFindingInterventionSerializer(many=True, required=False)

    class Meta:
        model = PatientFinding
        fields = [
            'id', 'patient_examination', 'finding',
            'classifications', 'interventions'
        ]

    def create(self, validated_data):
        """
        Create a new PatientFinding instance along with any provided nested classifications and interventions.
        
        Parameters:
            validated_data (dict): Data validated by the serializer, potentially including nested classifications and interventions.
        
        Returns:
            PatientFinding: The newly created PatientFinding instance with related nested objects.
        """
        classifications_data = validated_data.pop('classifications', [])
        interventions_data = validated_data.pop('interventions', [])

        patient_finding = PatientFinding.objects.create(**validated_data)

        # Erstelle verschachtelte Objekte
        self._create_nested_objects(patient_finding, classifications_data, interventions_data)

        return patient_finding

    def update(self, instance, validated_data):
        """
        Update a PatientFinding instance and its nested classifications and interventions.
        
        If new classification or intervention data is provided, existing related objects are deleted and replaced with the new data. Returns the updated PatientFinding instance.
        """
        classifications_data = validated_data.pop('classifications', None)
        interventions_data = validated_data.pop('interventions', None)

        # Update Hauptobjekt
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update verschachtelte Objekte wenn bereitgestellt
        if classifications_data is not None:
            instance.classifications.all().delete()
            self._create_classifications(instance, classifications_data)

        if interventions_data is not None:
            instance.interventions.all().delete()
            self._create_interventions(instance, interventions_data)

        return instance

    def _create_nested_objects(self, patient_finding, classifications_data, interventions_data):
        """
        Create nested classification and intervention objects linked to a PatientFinding instance.
        
        This helper method delegates the creation of related classification and intervention objects using the provided data, associating them with the specified PatientFinding.
        """
        self._create_classifications(patient_finding, classifications_data)
        self._create_interventions(patient_finding, interventions_data)

    def _create_classifications(self, patient_finding, classifications_data):
        """
        Create and associate classification objects with a given PatientFinding instance using provided data.
        
        Each classification in `classifications_data` is validated and saved as a related object to `patient_finding`. Validation errors are printed but do not interrupt processing.
        """
        for classification_data in classifications_data:
            serializer = PatientFindingClassificationSerializer(data=classification_data, context={'patient_finding': patient_finding})
            if serializer.is_valid():
                serializer.save(patient_finding=patient_finding)
            else:
                # Handle validation errors
                print(serializer.errors)  # Or raise an exception

    def _create_interventions(self, patient_finding, interventions_data):
        """
        Create and associate intervention objects with a given patient finding using provided intervention data.
        
        Parameters:
            patient_finding: The PatientFinding instance to associate interventions with.
            interventions_data: Iterable of dictionaries containing intervention attributes.
        """
        for intervention_data in interventions_data:
            PatientFindingIntervention.objects.create(
                finding=patient_finding,
                **intervention_data
            )

    def validate(self, data):
        """
        Validate that the selected finding is permitted for the specified patient examination.
        
        Raises a ValidationError if the finding is not among the allowed findings for the examination.
        
        Returns:
            dict: The validated data if the finding is permitted.
        """
        patient_examination = data.get('patient_examination')
        finding = data.get('finding')

        # Prüfe ob Finding für diese Examination erlaubt ist
        if finding and patient_examination:
            if finding not in patient_examination.examination.get_available_findings():
                raise serializers.ValidationError(
                    f"Finding '{finding.name}' ist nicht für Examination '{patient_examination.examination.name}' erlaubt."
                )

        return data