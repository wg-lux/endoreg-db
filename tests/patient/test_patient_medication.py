from endoreg_db.models import (
    Patient, 
    Center, Event,
    PatientLabValue, PatientFinding, FindingMorphologyClassification,
    Finding,
    FindingLocationClassification,
    PatientMedication, PatientMedicationSchedule, MedicationIndication,
    MedicationIndicationType, MedicationSchedule
)
from datetime import datetime

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_CENTER_NAME,
    TEST_PATIENT_MEDICATION_OUTPUT_PATH,
    TEST_ANTICOAGULATION_INDICATION_TYPES
)

# Note
# A MedicationIndication can have multiple MedicationSchedules 
# (e.g., pulmonary embolism medication starts with high dose for 10d and is then reduced)

# -> A MedicationSchedule defines the dosage, intake_times, and duration for ONE DRUG

# A PatientMedication has a patient as foreign key and may point to one MedicationSchedule

# A PatientMedicationSchedule has a patient as foreign key and may point to multiple PatientMedications


# PatientMedicationSchedule may point to multiple PatientMedications

#### Example
# - model: endoreg_db.medication_indication
#   fields:
#     name: "te_prevention-after_hip-apixaban"
#     events: ["hip_replacement"]
#     medication_schedules: [
#       "apixaban-2.5mg-twice_daily_32d", # min 32d, max 38d
#     ]
#     indication_type: "thromboembolism-prevention-after_hip_replacement"
#     sources: [
#       "fachinformation-apixaban-eliquis_2.5mg-bristol_meyer_squibbs"
#     ]

# Workflow to create a PatientMedicationSchedule for a single MedicationIndication:
# get medication_indication object and use classmethod create_by_patient_and_indication


class TestGeneratePatientMedication(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_disease_data", stdout=out)
        call_command("load_event_data", stdout=out)
        call_command("load_medication_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_patient_anticoagulation(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)
        out = ""

        pms = PatientMedicationSchedule.objects.create(
            patient = patient,
        )


        for indication_type in TEST_ANTICOAGULATION_INDICATION_TYPES:
            out += f"IndicationType - {indication_type}:\n" 
            indication_type = MedicationIndicationType.objects.get(name=indication_type)
            medication_indications = MedicationIndication.objects.filter(
                indication_type=indication_type
            )
        

            for medication_indication in medication_indications:
                medication_schedules = medication_indication.medication_schedules.all()
                out += f"\tMedicationIndication - {medication_indication}:\n"

                for medication_schedule in medication_schedules:
                    out += f"\t\tMedicationSchedule - {medication_schedule}:\n"
                    _patient_medication = pms.create_patient_medication_from_medication_schedule(
                        medication_schedule=medication_schedule,
                        medication_indication = medication_indication,
                        start_date=datetime.now()
                    )

                    out += f"\t\t\tPatientMedication - {_patient_medication}\n"

        pms = PatientMedicationSchedule.objects.get(pk = pms.pk)
        out += f"\n---\nResulting PatientMedicationSchedule:\n{pms}\n"
        out += "\n---\nPatientMedications:\n"
        for pm in pms.get_patient_medication():
            out += f"\t{pm}\n"
                


        with open(TEST_PATIENT_MEDICATION_OUTPUT_PATH, "w") as f:
            _ = out
            f.write(_)
