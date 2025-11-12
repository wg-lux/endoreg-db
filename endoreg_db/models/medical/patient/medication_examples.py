from endoreg_db.models import (
    Medication, 
    MedicationIndication, 
    MedicationIndicationType, 
    MedicationIntakeTime
)
from endoreg_db.helpers.default_objects import generate_patient

available_medications = Medication.objects.all()

# get common intake times
common_intake_times = MedicationIntakeTime.objects.filter(
    name__in=[
        'daily-morning', 
        'daily-noon', 
        'daily-evening', 
        'daily-night'
    ]
)

# Alternatively, we can use classmethods
daily_morning = MedicationIntakeTime.dm()
daily_noon = MedicationIntakeTime.dno()
daily_evening = MedicationIntakeTime.de()
daily_night = MedicationIntakeTime.dn()

# get random medication indication
mi = MedicationIndication.objects.order_by('?').first()

# Alternatively, we can use a specific indication type
medication_indication_type = MedicationIndicationType.objects.filter(indication_type='thromboembolism-prevention-non_valvular_af').order_by('?').first()

patient = generate_patient()

