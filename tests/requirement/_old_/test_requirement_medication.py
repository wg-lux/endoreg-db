\
from django.test import TestCase
from logging import getLogger
from endoreg_db.models import (
    Requirement,
    Medication,
    MedicationIndication,
    MedicationIndicationType,
    MedicationIntakeTime,
    MedicationSchedule,
    PatientMedication,
    PatientMedicationSchedule,
    Unit,
)
from ..helpers.data_loader import load_data
from ..helpers.default_objects import generate_patient

logger = getLogger(__name__)

class RequirementMedicationTest(TestCase):
    # Define requirement names that are expected to be in the DB via data fixtures
    # These would check if a Requirement's M2M field (e.g., Requirement.medications) is populated with the named entity.
    # Evaluation of these typically happens against the model instance itself (e.g. Medication instance).
    MEDICATION_REQUIREMENT_NAMES = [
        "medication_aspirin_is_defined_in_requirement", # Example name
        "medication_apixaban_is_defined_in_requirement",
    ]
    MEDICATION_INDICATION_REQUIREMENT_NAMES = [
        "indication_non_valvular_af_apixaban_is_defined_in_requirement",
    ]
    MEDICATION_INTAKE_TIME_REQUIREMENT_NAMES = [
        "intake_time_daily_morning_is_defined_in_requirement",
    ]
    MEDICATION_SCHEDULE_REQUIREMENT_NAMES = [
        "schedule_apixaban_5mg_bid_is_defined_in_requirement",
    ]

    # Requirements that evaluate a Patient's medications
    PATIENT_MEDICATION_REQUIREMENT_NAMES = [
        "patient_has_medication_aspirin",
        "patient_has_medication_apixaban_for_non_valvular_af", # Checks med & indication
        "patient_medication_intake_includes_daily_morning",
        "patient_schedule_contains_apixaban_5mg_bid_profile", # Checks if any PatientMedication in schedule matches profile
    ]

    MATCH_ANY_OPERATOR_NAME = "models_match_any"

    @classmethod
    def setUpTestData(cls):
        load_data() 

        cls.patient1 = generate_patient(first_name="MedTestAlice", last_name="PatientA")
        cls.patient1.save()
        cls.patient2 = generate_patient(first_name="MedTestBob", last_name="PatientB")
        cls.patient2.save()

        cls.med_aspirin = Medication.objects.get(name="aspirin")
        cls.med_apixaban = Medication.objects.get(name="apixaban")
        cls.unit_mg = Unit.objects.get(name="miligram") # Changed from name__iexact="mg"
        cls.it_daily_morning = MedicationIntakeTime.dm()
        cls.it_daily_evening = MedicationIntakeTime.de()

        cls.schedule_apixaban_5mg_bid_template = MedicationSchedule.objects.get(name="apixaban-5mg-twice_daily")
        
        # Create PatientMedication for Patient1
        cls.pm1_aspirin = PatientMedication.objects.create(
            patient=cls.patient1,
            medication=cls.med_aspirin,
            # medication_indication=cls.indication_general_prophylaxis,
            dosage=100,
            unit=cls.unit_mg
        )
        cls.pm1_aspirin.intake_times.add(cls.it_daily_morning)
        cls.pm1_aspirin.save()

        cls.pm1_apixaban = PatientMedication.objects.create(
            patient=cls.patient1,
            medication=cls.med_apixaban,
            # medication_indication=cls.indication_non_valvular_af,
            dosage=5, # Matches the schedule template dose
            unit=cls.unit_mg # Matches the schedule template unit
        )
        # Matches the schedule template intake times
        cls.pm1_apixaban.intake_times.add(cls.it_daily_morning, cls.it_daily_evening) 
        cls.pm1_apixaban.save()
        
        # Create PatientMedicationSchedule for Patient1
        cls.pms1 = PatientMedicationSchedule.objects.create(patient=cls.patient1)
        cls.pms1.medication.add(cls.pm1_aspirin, cls.pm1_apixaban)
        cls.pms1.save()

        # Patient2 has no medications initially

    def _test_requirements_exist(self, requirement_names_list, expected_primary_req_type_str, expected_secondary_req_type_str_list=None):
        for req_name in requirement_names_list:
            try:
                req = Requirement.objects.get(name=req_name)
                self.assertIsNotNone(req, f"Requirement '{req_name}' should exist.")
                self.assertIn(self.MATCH_ANY_OPERATOR_NAME, [op.name for op in req.operators.all()], f"Req {req_name} missing operator")
                
                actual_req_type_names = [rt.name for rt in req.requirement_types.all()]
                self.assertIn(expected_primary_req_type_str, actual_req_type_names, f"Req '{req_name}' missing primary type '{expected_primary_req_type_str}'")
                if expected_secondary_req_type_str_list:
                    for ert_str in expected_secondary_req_type_str_list:
                         self.assertIn(ert_str, actual_req_type_names, f"Requirement '{req_name}' missing secondary type '{ert_str}'")

            except Requirement.DoesNotExist:
                self.fail(f"Requirement '{req_name}' does not exist.")

    def test_direct_model_requirements_exist(self):
        self._test_requirements_exist(self.MEDICATION_REQUIREMENT_NAMES, "medication")
        self._test_requirements_exist(self.MEDICATION_INDICATION_REQUIREMENT_NAMES, "medication_indication")
        self._test_requirements_exist(self.MEDICATION_INTAKE_TIME_REQUIREMENT_NAMES, "medication_intake_time")
        self._test_requirements_exist(self.MEDICATION_SCHEDULE_REQUIREMENT_NAMES, "medication_schedule")

    # def test_patient_medication_related_requirements_exist(self):
    #     # These requirements are evaluated against a Patient (primary)
    #     # and use PatientMedication or PatientMedicationSchedule as secondary instances.
    #     self._test_requirements_exist(
    #         self.PATIENT_MEDICATION_REQUIREMENT_NAMES,
    #         "patient", 
    #         ["patient_medication", "patient_medication_schedule"] # Secondary types can be one of these
    #     )
    #     # You might want more granular checks per requirement if types are strictly one or the other
    #     req_schedule_check = Requirement.objects.get(name="patient_schedule_contains_apixaban_5mg_bid_profile")
    #     self.assertIn("patient_medication_schedule", [rt.name for rt in req_schedule_check.requirement_types.all()])
    #     self.assertIn("patient", [rt.name for rt in req_schedule_check.requirement_types.all()])


    def test_evaluate_patient_has_medication_aspirin(self):
        req = Requirement.objects.get(name="patient_has_medication_aspirin")
        # Assumed Requirement YAML: medications: [aspirin]

        self.assertTrue(req.evaluate(self.patient1, self.pm1_aspirin, mode="loose"), "P1 Aspirin PM")
        patient1_meds_qs = PatientMedication.objects.filter(patient=self.patient1)
        self.assertTrue(req.evaluate(self.patient1, patient1_meds_qs, mode="loose"), "P1 All PMs QS")
        self.assertTrue(req.evaluate(self.patient1, self.pms1, mode="loose"), "P1 PMS")
        self.assertTrue(req.evaluate(self.patient1, mode="loose"), "P1 loose")

        patient2_meds_qs = PatientMedication.objects.filter(patient=self.patient2)
        self.assertFalse(req.evaluate(self.patient2, patient2_meds_qs, mode="loose"), "P2 No PMs QS")
        pms2, _ = PatientMedicationSchedule.objects.get_or_create(patient=self.patient2)
        self.assertFalse(req.evaluate(self.patient2, pms2, mode="loose"), "P2 Empty PMS")
        self.assertFalse(req.evaluate(self.patient2, mode="loose"), "P2 loose no meds")

    def test_evaluate_patient_has_medication_apixaban_for_non_valvular_af(self):
        req = Requirement.objects.get(name="patient_has_medication_apixaban_for_non_valvular_af")
        # Assumed Requirement YAML: medications: [apixaban], medication_indications: [te_prevention-non_valvular_af-apixaban]

        self.assertTrue(req.evaluate(self.patient1, self.pm1_apixaban, mode="loose"), "P1 Apixaban PM")
        patient1_meds_qs = PatientMedication.objects.filter(patient=self.patient1)
        self.assertTrue(req.evaluate(self.patient1, patient1_meds_qs, mode="loose"), "P1 All PMs QS for Apixaban/Ind")
        self.assertTrue(req.evaluate(self.patient1, self.pms1, mode="loose"), "P1 PMS for Apixaban/Ind")
        self.assertTrue(req.evaluate(self.patient1, mode="loose"), "P1 loose for Apixaban/Ind")

        self.assertFalse(req.evaluate(self.pm1_aspirin, mode="loose"), "P1 Aspirin PM vs Apixaban/Ind Req")

        pms2, _ = PatientMedicationSchedule.objects.get_or_create(patient=self.patient2)
        self.assertFalse(req.evaluate(self.patient2, pms2, mode="loose"), "P2 Empty PMS vs Apixaban/Ind Req")
        self.assertFalse(req.evaluate(self.patient2, mode="loose"), "P2 loose no meds vs Apixaban/Ind Req")

    def test_evaluate_patient_medication_intake_includes_daily_morning(self):
        req = Requirement.objects.get(name="patient_medication_intake_includes_daily_morning")
        # Assumed Requirement YAML: medication_intake_times: [daily-morning]

        self.assertTrue(req.evaluate(self.patient1, mode="loose"), "P1 Aspirin (morning) PM")
        self.assertTrue(req.evaluate(self.pm1_apixaban, mode="loose"), "P1 Apixaban (morning/evening) PM")
        self.assertTrue(req.evaluate(self.pms1, mode="loose"), "P1 PMS (contains morning meds)")
        
        pm2_evening_only = PatientMedication.objects.create(
            patient=self.patient2, medication=self.med_aspirin, dosage=10, unit=self.unit_mg
        )
        pm2_evening_only.intake_times.add(self.it_daily_evening)
        pm2_evening_only.save()
        self.assertFalse(req.evaluate(self.patient2, pm2_evening_only, mode="loose"), "P2 Evening only PM")
        
        pms2, _ = PatientMedicationSchedule.objects.get_or_create(patient=self.patient2)
        pms2.medication.add(pm2_evening_only)
        pms2.save()
        self.assertFalse(req.evaluate(self.patient2, pms2, mode="loose"), "P2 PMS with evening only med")


    # def test_evaluate_patient_schedule_contains_apixaban_5mg_bid_profile(self):
    #     req = Requirement.objects.get(name="patient_schedule_contains_apixaban_5mg_bid_profile")
    #     # Assumed Requirement YAML: medication_schedules: [apixaban-5mg-twice_daily]
    #     # This test relies on the operator being able to match a PatientMedication's details
    #     # (med, dose, unit, intake_times) against a MedicationSchedule template.

    #     # P1's schedule (pms1) contains pm1_apixaban, which matches the profile of schedule_apixaban_5mg_bid_template
    #     self.assertTrue(req.evaluate(self.patient1, self.pms1, mode="loose"), "P1 PMS with matching Apixaban profile")
    #     self.assertTrue(req.evaluate(self.patient1, mode="loose"), "P1 loose with matching Apixaban profile in schedule")

    #     pms2, _ = PatientMedicationSchedule.objects.get_or_create(patient=self.patient2)
    #     self.assertFalse(req.evaluate(self.patient2, pms2, mode="loose"), "P2 Empty PMS")
    #     self.assertFalse(req.evaluate(self.patient2, mode="loose"), "P2 loose, no matching schedule")

    #     # Add a non-matching medication to patient2's schedule
    #     pm2_aspirin = PatientMedication.objects.create(
    #         patient=self.patient2, medication=self.med_aspirin, dosage=100, unit=self.unit_mg
    #     )
    #     pm2_aspirin.intake_times.add(self.it_daily_morning)
    #     pms2.medication.add(pm2_aspirin)
    #     pms2.save()
    #     self.assertFalse(req.evaluate(self.patient2, pms2, mode="loose"), "P2 PMS with non-matching med")
