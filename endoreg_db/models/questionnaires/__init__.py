from django.db import models

YES_NO_CHOICES = (
    ('yes', 'Ja'),
    ('no', 'Nein'),
)

class TtoQuestionnaire(models.Model):

    # Patient Information
    patient_name = models.CharField(max_length=255, verbose_name="Identifikation des Patienten (Name)")
    birth_date = models.DateField(verbose_name="Geburtsdatum")

    # Base documentation
    emergency_patient = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Notfallpatient/kürzlich untersuchter Patient (Verzicht auf Team-Time-Out möglich)")
    consent_signed = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Einverständniserklärung unterschrieben (Arzt, Patient)")
    documents_present = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Alle Dokumente liegen vor (Labor, Befunde, etc.)")
    communication_possible = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Kommunikation mit Patient möglich")
    work_incapacity_certificate = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Arbeitsunfähigkeitsbescheinigung")

    # priority items
    pregnancy = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Schwangerschaft")
    asa_classification_checked = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="ASA-Klassifikation/Komorbidität geprüft")
    previous_anesthesia_complications = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Komplikationen bei bisherigen Narkosen?")
    last_meal_over_6_hours_ago = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Zeitpunkt letzte Mahlzeit > 6 Stunden")
    allergies = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Allergien, welche?")
    outpatient_accompaniment_post_sedation = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Nur bei ambulanter Vorstellung: Begleitung nach Sedierung?")
    
    # Possessions
    dental_prosthesis = models.BooleanField(default=False, verbose_name="Zahnprothese")
    glasses = models.BooleanField(default=False, verbose_name="Brille")
    implants = models.BooleanField(default=False, verbose_name="Implantate")
    hearing_aids = models.BooleanField(default=False, verbose_name="Hörgeräte")
    

    # Medical History
    anticoagulants_ass = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Antikoagulation, ASS")
    blood_pressure_medication = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Blutdruckmedikamente")
    glaucoma = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Glaukom")

    metal_implants = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Metallimplantate")
    pacemaker_icd = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Herzschrittmacher/ICD")
    
    copd = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="COPD")
    liver_cirrhosis = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Leberzirrhose")
    
    ibd = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="CED (Chronisch entzündliche Darmerkrankungen)")
    radiation = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Bestrahlung")
    surgeries = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="OP´s")

    # preflight
    team_introduction = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Teamvorstellung mit Name und Aufgabe")
    instruments_available = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Notwendige Instrumente vorhanden?")
    monitoring_medications_equipment_checked = models.CharField(max_length=3, choices=YES_NO_CHOICES, verbose_name="Monitoring, Medikamente, Equipment zum Atemwegsmanagement zur Verfügung und überprüft?")
    complete_documentation_inclusive_care_notes = models.TextField(verbose_name="Vollständige Dokumentation inklusive Hinweise für Nachsorge")
    
    # notes
    notes = models.TextField(verbose_name="Bemerkungen", default = "Keine Bemerkungen")
    
    # postflight  
    specimens_secured = models.BooleanField(default=False, verbose_name="Histologische Proben gesichert")
    patient_condition_documented = models.TextField(verbose_name="Patientenzustand dokumentiert (je nach Ausgangszustand)")

    

    def __str__(self):
        return f"Endoscopy Questionnaire for {self.patient_name} on {self.birth_date}"
    
    def get_id_attributes(self):
        _ = [self.patient_name, self.birth_date]
        return _
    
    def get_base_documentation_attributes(self):
        _ = [self.emergency_patient, self.consent_signed, self.documents_present, self.communication_possible, self.work_incapacity_certificate]
        return _
    
    def get_priority_items_attributes(self):
        _ = [self.pregnancy, self.asa_classification_checked, self.previous_anesthesia_complications, self.last_meal_over_6_hours_ago, self.allergies, self.outpatient_accompaniment_post_sedation]
        return _
    
    def get_possessions_attributes(self):
        _ = [self.dental_prosthesis, self.glasses, self.implants, self.hearing_aids]
        return _
    
    def get_medical_history_attributes(self): 
        _ = [
            self.anticoagulants_ass,
            self.blood_pressure_medication,
            self.glaucoma,
            self.metal_implants,
            self.pacemaker_icd,
            self.copd,
            self.liver_cirrhosis,
            self.ibd,
            self.radiation,
            self.surgeries,
        ]

    def get_preflight_attributes(self):
        _ = [
            self.team_introduction,
            self.instruments_available,
            self.monitoring_medications_equipment_checked,
        ]

    def get_note_attributes(self):
        _ = [self.notes]

    def get_postflight_attributes(self):
        _ = [
            self.specimens_secured,
            self.patient_condition_documented
        ]

