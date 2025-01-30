from endoreg_db.models import (
    Patient, 
    Center, Disease,
    PatientDisease,
)
    
from datetime import datetime

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_CENTER_NAME,
    TEST_PATIENT_DISEASES_OUTPUT_PATH
)

class TestPatientDiseases(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_disease_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_patient_disease(self):
        patient = Patient.create_generic(center=TEST_CENTER_NAME)

        # get all diseases
        diseases = Disease.objects.all()

        out = ""

        for disease in diseases:
            classifications = disease.get_classifications()
            out += f"{disease.name}:\n"

            for classification in classifications:
                choices = classification.get_choices()
                out += f"\t{classification.name}:\n"

                for choice in choices:

                    _patient_disease = PatientDisease.objects.create(
                        patient=patient,
                        disease=disease,
                        start_date=datetime.now()
                    )

                    out += f"\t\t{choice.name}\n"
                    out += f"\t\t\t{_patient_disease}\n\n"

        with open(TEST_PATIENT_DISEASES_OUTPUT_PATH, "w") as f:
            f.write(out)


        



