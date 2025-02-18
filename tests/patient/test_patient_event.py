from endoreg_db.models import (
    Patient, 
    Center, Event,
)
from datetime import datetime
from .utils import create_test_patient
from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_CENTER_NAME,
    TEST_PATIENT_EVENT_OUTPUT_PATH
)

class TestGeneratePatientEvents(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_disease_data", stdout=out)
        call_command("load_event_data", stdout=out)
        Center.objects.create(name=TEST_CENTER_NAME)

    def test_patient_event(self):
        patient = create_test_patient(center_name=TEST_CENTER_NAME)

        # get all Events
        events = Event.objects.all()

        out = ""

        for event in events:
            _patient_event = patient.create_event(
                event_name_str=event.name,
                date_start=datetime.now()
            )

            out += f"{event.name}:\n"
            out += f"\t{_patient_event}\n\n"

        with open(TEST_PATIENT_EVENT_OUTPUT_PATH, "w") as f:
            f.write(out)
