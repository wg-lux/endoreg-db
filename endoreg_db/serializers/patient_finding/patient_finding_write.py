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
        locations_data = validated_data.pop('locations', [])
        morphologies_data = validated_data.pop('morphologies', [])
        interventions_data = validated_data.pop('interventions', [])

        patient_finding = PatientFinding.objects.create(**validated_data)

        # Erstelle verschachtelte Objekte
        self._create_nested_objects(patient_finding, locations_data, morphologies_data, interventions_data)

        return patient_finding

    def update(self, instance, validated_data):
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

    def _create_nested_objects(self, patient_finding, locations_data, morphologies_data, interventions_data):
        """Hilfsmethode für verschachtelte Objekterstellung"""
        self._create_classifications(patient_finding, locations_data)
        self._create_interventions(patient_finding, interventions_data)

    def _create_classifications(self, patient_finding, classifications_data):
        for classification_data in classifications_data:
            PatientFindingClassificationSerializer.create(
                PatientFindingClassificationSerializer(),
                validated_data=classification_data,
                instance=patient_finding
            )

    def _create_interventions(self, patient_finding, interventions_data):
        for intervention_data in interventions_data:
            PatientFindingIntervention.objects.create(
                finding=patient_finding,
                **intervention_data
            )

    def validate(self, data):
        """Geschäftslogik-Validierung"""
        patient_examination = data.get('patient_examination')
        finding = data.get('finding')

        # Prüfe ob Finding für diese Examination erlaubt ist
        if finding and patient_examination:
            if finding not in patient_examination.examination.get_available_findings():
                raise serializers.ValidationError(
                    f"Finding '{finding.name}' ist nicht für Examination '{patient_examination.examination.name}' erlaubt."
                )

        return data