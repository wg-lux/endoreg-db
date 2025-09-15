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
    
    # 🎯 NEW: Patient birth date and gender fields for age calculation
    patient_birth_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Patient's birth date for age calculation during requirement evaluation"
    )
    
    patient_gender = serializers.CharField(
        max_length=10,
        required=False,
        allow_null=True,
        help_text="Patient's gender for requirement evaluation"
    )
    
    # Zusätzliche schreibgeschützte Felder für bessere API-Antworten
    patient_name = serializers.SerializerMethodField()
    examination_name = serializers.SerializerMethodField()
    
    class Meta:
        model = PatientExamination
        fields = [
            'id', 'patient', 'patient_data', 'examination', 'date_start', 'date_end', 
            'hash', 'patient_name', 'examination_name',
            # 🎯 NEW: Include patient birth date and gender fields
            'patient_birth_date', 'patient_gender'
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
        """
        Update an existing PatientExamination instance with validated data.
        
        If a new patient is provided, updates the patient reference. Applies all other validated fields to the instance and saves changes.
        
        Returns:
            PatientExamination: The updated PatientExamination instance.
        
        Raises:
            ValidationError: If an error occurs during the update process.
        """
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
    
    def get_findings(self, patient_examination_id):
        """Gibt die zugehörigen Befunde zurück"""
        obj = PatientExamination.get_or_create_patient_examination_by_id(PatientExamination, patient_examination_id)
        self.instance = obj
        response = PatientExaminationSerializer()
        return self.instance.patient_findings.all() if self.instance else []
