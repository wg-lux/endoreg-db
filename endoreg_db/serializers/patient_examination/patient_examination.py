from rest_framework import serializers
from endoreg_db.models import PatientExamination, Patient, Examination
from datetime import date

class PatientExaminationSerializer(serializers.ModelSerializer):
    # Verwende CharField für patient, um patient_hash zu empfangen
    patient = serializers.CharField(
        write_only=True,  # Nur für Eingabe verwenden
        required=True,
        help_text="Patient Hash (z.B. 'patient_2')"
    )
    
    # Für die Ausgabe verwenden wir ein schreibgeschütztes Feld
    patient_data = serializers.SerializerMethodField(read_only=True)
    
    examination = serializers.SlugRelatedField(
        slug_field='name',
        queryset=Examination.objects.all(),
        required=False,
        allow_null=True
    )
    
    # Zusätzliche schreibgeschützte Felder für bessere API-Antworten
    patient_name = serializers.SerializerMethodField()
    examination_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PatientExamination
        fields = [
            'id', 'patient', 'patient_data', 'examination', 'date_start', 'date_end', 
            'hash', 'patient_name', 'examination_name'
        ]
        read_only_fields = ['id', 'hash', 'patient_name', 'examination_name', 'patient_data']

    def get_patient_data(self, obj):
        """Gibt die Patient-Daten für die Ausgabe zurück"""
        if obj.patient:
            return {
                'id': obj.patient.id,
                'patient_hash': obj.patient.patient_hash,
                'first_name': obj.patient.first_name,
                'last_name': obj.patient.last_name
            }
        return None

    def get_patient_name(self, obj):
        """Gibt den vollständigen Namen des Patienten zurück"""
        if obj.patient:
            return f"{obj.patient.first_name} {obj.patient.last_name}"
        return None
    
    def get_examination_name(self, obj):
        """Gibt den Namen der Untersuchung zurück"""
        if obj.examination:
            return obj.examination.name
        return None

    def validate_patient(self, value):
        """Validiert und erstellt Patient falls nötig"""
        if not value:
            raise serializers.ValidationError("Patient Hash ist erforderlich")
        
        # Versuche Patient zu finden
        try:
            patient = Patient.objects.get(patient_hash=value)
            return patient
        except Patient.DoesNotExist:
            # Erstelle automatisch einen Pseudo-Patienten
            patient = Patient.objects.create(
                patient_hash=value,
                first_name="Patient",
                last_name=value,
                is_real_person=False  # Markiere als Pseudo-Patient
            )
            return patient

    def validate_date_start(self, value):
        """Validiert das Startdatum"""
        if value and value > date.today():
            raise serializers.ValidationError("Startdatum kann nicht in der Zukunft liegen")
        return value

    def validate_date_end(self, value):
        """Validiert das Enddatum"""
        if value and value > date.today():
            raise serializers.ValidationError("Enddatum kann nicht in der Zukunft liegen")
        return value

    def validate(self, data):
        """Validiert die gesamten Daten"""
        date_start = data.get('date_start')
        date_end = data.get('date_end')
        
        if date_start and date_end and date_end < date_start:
            raise serializers.ValidationError("Enddatum muss nach dem Startdatum liegen")
        
        return data

    def create(self, validated_data):
        """Erstellt eine neue PatientExamination mit verbesserter Fehlerbehandlung"""
        try:
            # Patient wurde bereits in validate_patient erstellt/gefunden
            patient = validated_data.pop('patient')  # Entferne patient aus validated_data
            validated_data['patient'] = patient  # Füge das Patient-Objekt hinzu
            
            patient_examination = PatientExamination.objects.create(**validated_data)
            return patient_examination
        except Exception as e:
            raise serializers.ValidationError(f"Fehler beim Erstellen der Patientenuntersuchung: {str(e)}")

    def update(self, instance, validated_data):
        """Aktualisiert eine bestehende PatientExamination"""
        try:
            # Falls Patient geändert wird
            if 'patient' in validated_data:
                patient = validated_data.pop('patient')
                validated_data['patient'] = patient
            
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            return instance
        except Exception as e:
            raise serializers.ValidationError(f"Fehler beim Aktualisieren der Patientenuntersuchung: {str(e)}")


# Vereinfachte Serializer für Dropdown-Listen
class PatientDropdownSerializer(serializers.ModelSerializer):
    """Serializer für Patient-Dropdown"""
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Patient
        fields = ['id', 'patient_hash', 'first_name', 'last_name', 'display_name', 'dob']
    
    def get_display_name(self, obj):
        """Gibt eine benutzerfreundliche Anzeige für den Patienten zurück"""
        return f"{obj.first_name} {obj.last_name} ({obj.patient_hash[:8]}...)"


class ExaminationDropdownSerializer(serializers.ModelSerializer):
    """Serializer für Examination-Dropdown"""
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Examination
        fields = ['id', 'name', 'name_de', 'name_en', 'display_name']
    
    def get_display_name(self, obj):
        """Gibt eine benutzerfreundliche Anzeige für die Untersuchung zurück"""
        return obj.name_de if obj.name_de else obj.name