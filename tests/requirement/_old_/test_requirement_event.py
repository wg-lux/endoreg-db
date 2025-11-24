import datetime
from django.test import TestCase
from logging import getLogger
from endoreg_db.models import (
    Requirement,
    RequirementOperator,
    Patient,
    Event,
    PatientEvent,
    Unit,
    RequirementType, # Added RequirementType
)
from ..helpers.data_loader import (
    load_data,
)
from ..helpers.default_objects import generate_patient

logger = getLogger(__name__)

class RequirementEventTest(TestCase):
    EVENT_REQUIREMENT_NAMES = [
        "event_coro_des_implantation_exists",
        "event_coro_bms_implantation_exists",
        "event_pulmonary_embolism_exists",
        "event_deep_vein_thrombosis_exists",
        "event_stroke_exists",
        "event_transient_ischemic_attack_exists",
        "event_hip_replacement_surgery_exists",
        "event_knee_replacement_surgery_exists",
        "event_recurrent_thrombembolism_exists",
        "event_stroke_in_last_30_days", # New timeframe requirement
    ]

    TIME_FRAME_OPERATOR_NAME = "models_match_any_in_timeframe"

    @classmethod
    def setUpTestData(cls):
        load_data()


        cls.patient1 = generate_patient()
        cls.patient1.save()
        cls.patient2 = generate_patient()
        cls.patient2.save()
        
        # Get event models
        cls.stroke_event = Event.objects.get(name="stroke")
        cls.des_event = Event.objects.get(name="coro_des_implantation")
        cls.pem_event = Event.objects.get(name="pulmonary_embolism")

        # Create some PatientEvent instances
        cls.patient1_stroke_recent = PatientEvent.objects.create(
            patient=cls.patient1,
            event=cls.stroke_event,
            date_start=datetime.date.today() - datetime.timedelta(days=10) # 10 days ago
        )
        cls.patient1_stroke_old = PatientEvent.objects.create(
            patient=cls.patient1,
            event=cls.stroke_event,
            date_start=datetime.date.today() - datetime.timedelta(days=400) # 400 days ago
        )
        cls.patient1_des = PatientEvent.objects.create(
            patient=cls.patient1,
            event=cls.des_event,
            date_start=datetime.date.today() - datetime.timedelta(days=50)
        )
        cls.patient2_pem = PatientEvent.objects.create(
            patient=cls.patient2,
            event=cls.pem_event,
            date_start=datetime.date.today() - datetime.timedelta(days=5)
        )
        
        # For timeframe tests
        cls.unit_days = Unit.objects.get(name="days")


    def test_event_requirements_exist(self):
        for req_name in self.EVENT_REQUIREMENT_NAMES:
            try:
                req = Requirement.objects.get(name=req_name)
                self.assertIsNotNone(req, f"Requirement '{req_name}' should exist.")
                if req_name == "event_stroke_in_last_30_days":
                    self.assertEqual(req.numeric_value_min, -30)
                    self.assertEqual(req.numeric_value_max, 0)
                    self.assertEqual(req.unit, self.unit_days)
                    self.assertIn(self.TIME_FRAME_OPERATOR_NAME, [op.name for op in req.operators.all()])
                else:
                    self.assertIn("models_match_any", [op.name for op in req.operators.all()])
                self.assertIn("patient", [rt.name for rt in req.requirement_types.all()])
                self.assertIn("patient_event", [rt.name for rt in req.requirement_types.all()])
            except Requirement.DoesNotExist:
                self.fail(f"Requirement '{req_name}' does not exist.")

    def test_timeframe_operator_exists(self):
        try:
            op = RequirementOperator.objects.get(name=self.TIME_FRAME_OPERATOR_NAME)
            self.assertIsNotNone(op, f"Operator '{self.TIME_FRAME_OPERATOR_NAME}' should exist.")
        except RequirementOperator.DoesNotExist:
            self.fail(f"Operator '{self.TIME_FRAME_OPERATOR_NAME}' does not exist.")

    def test_event_stroke_exists_requirement(self):
        req = Requirement.objects.get(name="event_stroke_exists")
        
        # Patient 1 has a stroke event
        self.assertTrue(req.evaluate(self.patient1, self.patient1_stroke_recent, mode="loose"))
        self.assertTrue(req.evaluate(self.patient1, PatientEvent.objects.filter(patient=self.patient1), mode="loose"))

        # Patient 2 does not have a stroke event
        self.assertFalse(req.evaluate(self.patient2, self.patient2_pem, mode="loose"))
        self.assertFalse(req.evaluate(self.patient2, PatientEvent.objects.filter(patient=self.patient2), mode="loose"))
        self.assertFalse(req.evaluate(self.patient2, mode="loose")) # No events for patient 2 that match

    def test_event_des_implantation_exists_requirement(self):
        req = Requirement.objects.get(name="event_coro_des_implantation_exists")
        # Patient 1 has DES event
        self.assertTrue(req.evaluate(self.patient1, self.patient1_des, mode="loose"))
        # Patient 2 does not
        self.assertFalse(req.evaluate(self.patient2, self.patient2_pem, mode="loose"))

    def test_event_stroke_in_last_30_days_requirement(self):
        req = Requirement.objects.get(name="event_stroke_in_last_30_days")

        # Patient 1 has a recent stroke (10 days ago)
        self.assertTrue(
            req.evaluate(self.patient1, self.patient1_stroke_recent, mode="loose"),
            "Patient 1 recent stroke should satisfy requirement."
        )
        # Evaluate with a queryset of PatientEvents for patient1
        patient1_events = PatientEvent.objects.filter(patient=self.patient1)
        self.assertTrue(
            req.evaluate(self.patient1, patient1_events, mode="loose"),
            "Patient 1 events (including recent stroke) should satisfy timeframe requirement."
        )

        # Patient 1 also has an old stroke (400 days ago), but the recent one should make it true
        # Create a specific input link for just the old stroke to test this path
        self.assertFalse(
            req.evaluate(self.patient1, self.patient1_stroke_old, mode="loose"),
            "Patient 1 old stroke alone should NOT satisfy timeframe requirement."
        )
        
        # Patient 2 has no stroke events
        patient2_events = PatientEvent.objects.filter(patient=self.patient2)
        self.assertFalse(
            req.evaluate(self.patient2, patient2_events, mode="loose"),
            "Patient 2 (no stroke) should NOT satisfy timeframe requirement."
        )
        self.assertFalse(
            req.evaluate(self.patient2, mode="loose"), # No relevant events for patient 2
            "Patient 2 (no stroke, empty event input) should NOT satisfy timeframe requirement."
        )

    def test_event_stroke_in_last_30_days_no_matching_event_type(self):
        req = Requirement.objects.get(name="event_stroke_in_last_30_days")
        # Patient1 has a DES implant, but the requirement is for STROKE
        self.assertFalse(
            req.evaluate(self.patient1, self.patient1_des, mode="loose"),
            "Requirement for stroke should not be met by DES event, even if date is fine."
        )

    def test_event_stroke_in_last_30_days_no_unit_on_requirement(self):
        req = Requirement.objects.get(name="event_stroke_in_last_30_days")
        original_unit = req.unit
        req.unit = None # Temporarily remove unit
        req.save()

        self.assertFalse(
            req.evaluate(self.patient1, self.patient1_stroke_recent, mode="loose"),
            "Timeframe requirement should fail if unit is missing."
        )
        
        req.unit = original_unit # Restore unit
        req.save()

    def test_event_stroke_in_last_30_days_no_numeric_min_on_requirement(self):
        req = Requirement.objects.get(name="event_stroke_in_last_30_days")
        original_min = req.numeric_value_min
        req.numeric_value_min = None 
        req.save()

        self.assertFalse(
            req.evaluate(self.patient1, self.patient1_stroke_recent, mode="loose"),
            "Timeframe requirement should fail if numeric_value_min is missing."
        )
        
        req.numeric_value_min = original_min
        req.save()

    def test_requirement_with_no_specific_events_in_links_and_timeframe_operator(self):
        # Create a temporary requirement that uses models_match_any_in_timeframe
        # but does NOT specify any particular events in its own .events list.
        # This tests if _evaluate_models_match_any_in_timeframe correctly handles
        # the case where requirement_links.active() might be empty for 'events'.
        
        op_timeframe = RequirementOperator.objects.get(name=self.TIME_FRAME_OPERATOR_NAME)
        # Fetch RequirementType instances
        rt_patient = RequirementType.objects.get(name="patient")
        rt_patient_event = RequirementType.objects.get(name="patient_event")

        temp_req = Requirement.objects.create(
            name="temp_event_any_in_timeframe_no_specific_event_links",
            numeric_value_min=-30,
            numeric_value_max=0,
            unit=self.unit_days
        )
        temp_req.operators.add(op_timeframe)
        temp_req.requirement_types.add(rt_patient, rt_patient_event) # Use instances

        # According to current logic in _evaluate_models_match_any_in_timeframe:
        # if not active_req_links_dict: return True (vacuously)
        # This means if the requirement itself doesn't list specific events to check for,
        # it will return True, as there are no *specific required events* that failed the timeframe.
        # The operator is "match ANY in timeframe". If the req doesn't specify *what* to match,
        # it's as if it's saying "is there any of *nothing specific* in this timeframe?"
        # This behavior might need refinement based on desired strictness.
        # For now, testing the current implementation.
        self.assertTrue(
            temp_req.evaluate(self.patient1, self.patient1_stroke_recent, mode="loose"),
            "Req with timeframe op but no specific event links should be vacuously true if active_req_links is empty."
        )
        
        # If we provide an event that is NOT in timeframe, it should still be true because the req
        # doesn't care *which* event, only that *if* it cared, it would be in timeframe.
        self.assertTrue(
            temp_req.evaluate(self.patient1, self.patient1_stroke_old, mode="loose"),
             "Req with timeframe op but no specific event links should be vacuously true even with old event."
        )

        # Clean up
        temp_req.delete()
