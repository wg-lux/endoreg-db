from rest_framework import serializers
from django.utils.translation import get_language
from endoreg_db.models import (
    PatientFinding, 
    PatientFindingLocation, 
    PatientFindingMorphology,
    PatientFindingIntervention,
    Finding,
)


class TranslatableFieldMixin:
    """Mixin für automatische Sprachauswahl basierend auf Accept-Language"""
    
    def get_localized_name(self, obj, field_base='name'):
        """Intelligente Sprachauswahl mit Fallback-Logik"""
        current_lang = get_language() or 'en'
        
        # Versuche sprachspezifisches Feld
        lang_field = f"{field_base}_{current_lang}"
        if hasattr(obj, lang_field):
            value = getattr(obj, lang_field)
            if value:
                return value
        
        # Fallback auf Deutsch
        de_field = f"{field_base}_de"
        if hasattr(obj, de_field):
            value = getattr(obj, de_field)
            if value:
                return value
                
        # Fallback auf Englisch
        en_field = f"{field_base}_en"
        if hasattr(obj, en_field):
            value = getattr(obj, en_field)
            if value:
                return value
                
        # Letzter Fallback auf Basis-Feld
        return getattr(obj, field_base, '')


class PatientFindingLocationSerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Optimierter Serializer für PatientFindingLocation"""
    
    location_classification_name = serializers.SerializerMethodField()
    location_choice_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PatientFindingLocation
        fields = [
            'id', 'location_classification', 'location_choice', 
            'location_classification_name', 'location_choice_name',
            'subcategories', 'numerical_descriptors'
        ]
    
    def get_location_classification_name(self, obj):
        return self.get_localized_name(obj.location_classification)
    
    def get_location_choice_name(self, obj):
        return self.get_localized_name(obj.location_choice)


class PatientFindingMorphologySerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Optimierter Serializer für PatientFindingMorphology"""
    
    morphology_classification_name = serializers.SerializerMethodField()
    morphology_choice_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PatientFindingMorphology
        fields = [
            'id', 'morphology_classification', 'morphology_choice',
            'morphology_classification_name', 'morphology_choice_name', 
            'subcategories', 'numerical_descriptors'
        ]
    
    def get_morphology_classification_name(self, obj):
        return self.get_localized_name(obj.morphology_classification)
    
    def get_morphology_choice_name(self, obj):
        return self.get_localized_name(obj.morphology_choice)


class PatientFindingInterventionSerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Optimierter Serializer für PatientFindingIntervention"""
    
    intervention_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PatientFindingIntervention
        fields = ['id', 'intervention', 'intervention_name', 'state']
    
    def get_intervention_name(self, obj):
        return self.get_localized_name(obj.intervention)


class PatientFindingDetailSerializer(serializers.ModelSerializer, TranslatableFieldMixin):
    """Vollständiger Serializer mit allen verschachtelten Daten - optimiert für N+1 Vermeidung"""
    
    finding_name = serializers.SerializerMethodField()
    locations = PatientFindingLocationSerializer(many=True, read_only=True)
    morphologies = PatientFindingMorphologySerializer(many=True, read_only=True)
    interventions = PatientFindingInterventionSerializer(many=True, read_only=True)
    
    # Meta-Informationen für das Frontend
    patient_id = serializers.CharField(source='patient_examination.patient.id', read_only=True)
    examination_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PatientFinding
        fields = [
            'id', 'patient_examination', 'finding', 'finding_name',
            'patient_id', 'examination_name',
            'locations', 'morphologies', 'interventions'
        ]
        
    def get_finding_name(self, obj):
        return self.get_localized_name(obj.finding)
    
    def get_examination_name(self, obj):
        return self.get_localized_name(obj.patient_examination.examination)


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
        return self.get_localized_name(obj.finding)


class PatientFindingWriteSerializer(serializers.ModelSerializer):
    """Schreiboptimierter Serializer mit Nested Write Support"""
    
    locations = PatientFindingLocationSerializer(many=True, required=False)
    morphologies = PatientFindingMorphologySerializer(many=True, required=False)
    interventions = PatientFindingInterventionSerializer(many=True, required=False)
    
    class Meta:
        model = PatientFinding
        fields = [
            'id', 'patient_examination', 'finding',
            'locations', 'morphologies', 'interventions'
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
        locations_data = validated_data.pop('locations', None)
        morphologies_data = validated_data.pop('morphologies', None)
        interventions_data = validated_data.pop('interventions', None)
        
        # Update Hauptobjekt
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Update verschachtelte Objekte wenn bereitgestellt
        if locations_data is not None:
            instance.locations.all().delete()
            self._create_locations(instance, locations_data)
            
        if morphologies_data is not None:
            instance.morphologies.all().delete()
            self._create_morphologies(instance, morphologies_data)
            
        if interventions_data is not None:
            instance.interventions.all().delete()
            self._create_interventions(instance, interventions_data)
        
        return instance
    
    def _create_nested_objects(self, patient_finding, locations_data, morphologies_data, interventions_data):
        """Hilfsmethode für verschachtelte Objekterstellung"""
        self._create_locations(patient_finding, locations_data)
        self._create_morphologies(patient_finding, morphologies_data)
        self._create_interventions(patient_finding, interventions_data)
    
    def _create_locations(self, patient_finding, locations_data):
        for location_data in locations_data:
            PatientFindingLocation.objects.create(
                finding=patient_finding,
                **location_data
            )
    
    def _create_morphologies(self, patient_finding, morphologies_data):
        for morphology_data in morphologies_data:
            PatientFindingMorphology.objects.create(
                finding=patient_finding,
                **morphology_data
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