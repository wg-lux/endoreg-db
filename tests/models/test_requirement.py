# from django.test import TestCase
# from datetime import date, timezone

# from endoreg_db.models import (
#     PatientExamination,
#     PatientLabValue,
#     PatientLabSample,
#     Requirement,
#     RequirementType,
#     RequirementOperator, # Added
#     LabValue,
#     Unit,
#     Disease,
#     PatientDisease,
#     Examination,
#     PatientLabSampleType,
#     Gender,
# )

# # Helper to load initial data if needed
# from ..helpers.data_loader import load_data
# # Helper to create default objects
# from ..helpers.default_objects import (
#     generate_patient,
# )

# class RequirementEvaluationTests(TestCase):
#     @classmethod
#     def setUpTestData(cls):
#         load_data()  # Load necessary base data (Genders, Units, etc.)

#         # Create common RequirementTypes
#         cls.rt_patient, _ = RequirementType.objects.get_or_create(name="Patient")
#         cls.rt_lab_value, _ = RequirementType.objects.get_or_create(name="PatientLabValue")
#         cls.rt_examination, _ = RequirementType.objects.get_or_create(name="PatientExamination")
#         cls.rt_lab_sample, _ = RequirementType.objects.get_or_create(name="PatientLabSample")

#         # Create common RequirementOperators
#         cls.op_match_all, _ = RequirementOperator.objects.get_or_create(name="models_match_all")
#         cls.op_lab_greater_than, _ = RequirementOperator.objects.get_or_create(name="lab_latest_numeric_greater_than_value")
#         # Add other operators if needed, e.g., for lower_than_value, specific string matches etc.
#         # cls.op_lab_lower_than, _ = RequirementOperator.objects.get_or_create(name="lab_latest_numeric_lower_than_value")


#         # Create common Gender
#         cls.gender_male, _ = Gender.objects.get_or_create(name="male")
#         cls.gender_female, _ = Gender.objects.get_or_create(name="female")

#         # Create a generic patient for some tests
#         cls.patient = generate_patient(
#             first_name="BaseTest",
#             last_name="Patient",
#             dob=date(1980, 1, 1),
#             gender=cls.gender_male
#         )
        
#         # Common Unit
#         cls.unit_mg_dl, _ = Unit.objects.get_or_create(name="mg/dL", symbol="mg/dL")
        
#         # Common Diseases
#         cls.disease_diabetes, _ = Disease.objects.get_or_create(name="Diabetes Mellitus", defaults={"icd_code": "E11"})
#         cls.disease_hypertension, _ = Disease.objects.get_or_create(name="Hypertension", defaults={"icd_code": "I10"})

#         # Common Examinations
#         cls.exam_colonoscopy, _ = Examination.objects.get_or_create(name="Colonoscopy")
#         cls.exam_gastroscopy, _ = Examination.objects.get_or_create(name="Gastroscopy")

#         # Common LabValue
#         cls.lab_glucose, _ = LabValue.objects.get_or_create(
#             name="Glucose", defaults={"default_unit": cls.unit_mg_dl, "numeric_precision": 0}
#         )
        
#         # Common PatientLabSampleType
#         cls.sample_type_blood, _ = PatientLabSampleType.objects.get_or_create(name="Blood")
#         cls.sample_type_urine, _ = PatientLabSampleType.objects.get_or_create(name="Urine")


#     def test_evaluate_requirement_with_patient_has_disease_success(self):
#         """Test Requirement: Patient must have a specific disease."""
#         requirement = Requirement.objects.create(
#             name="PatientHasDiabetes",
#             description="The patient must be diagnosed with Diabetes Mellitus."
#         )
#         requirement.requirement_types.add(self.rt_patient)
#         requirement.diseases.add(self.disease_diabetes)
#         requirement.operators.add(self.op_match_all)

#         test_patient = generate_patient(first_name="John", last_name="Doe", gender=self.gender_male, dob=date(1970, 5, 5))
#         PatientDisease.objects.create(patient=test_patient, disease=self.disease_diabetes)

#         result = requirement.evaluate(test_patient, mode="strict")
#         self.assertTrue(result, "Requirement should be met for patient with Diabetes.")

#     def test_evaluate_requirement_with_patient_has_disease_failure(self):
#         """Test Requirement: Patient does NOT have the specific disease."""
#         requirement = Requirement.objects.create(
#             name="PatientHasHypertension",
#             description="The patient must be diagnosed with Hypertension."
#         )
#         requirement.requirement_types.add(self.rt_patient)
#         requirement.diseases.add(self.disease_hypertension)
#         requirement.operators.add(self.op_match_all)

#         test_patient = generate_patient(first_name="Jane", last_name="Doe", gender=self.gender_female, dob=date(1975, 6, 6))
#         PatientDisease.objects.create(patient=test_patient, disease=self.disease_diabetes) # Has Diabetes, not Hypertension

#         result = requirement.evaluate(test_patient, mode="strict")
#         self.assertFalse(result, "Requirement should NOT be met for patient without Hypertension.")

#     def test_evaluate_requirement_with_patient_lab_value_greater_than_success(self):
#         """Test Requirement: Lab value (Glucose) is greater than a specific value."""
#         requirement = Requirement.objects.create(
#             name="GlucoseGreaterThan100",
#             description="Glucose level must be greater than 100 mg/dL.",
#             numeric_value=100.0 
#         )
#         requirement.requirement_types.add(self.rt_lab_value)
#         requirement.lab_values.add(self.lab_glucose) 
#         requirement.operators.add(self.op_lab_greater_than)

#         patient_lab_value = PatientLabValue.objects.create(
#             patient=self.patient,
#             lab_value=self.lab_glucose,
#             value=150.0,
#             unit=self.unit_mg_dl,
#             datetime=timezone.now()
#         )

#         result = requirement.evaluate(
#             patient_lab_value, 
#             mode="strict",
#             requirement_model=requirement, # Kwarg for lab operator
#             patient_lab_values=[patient_lab_value] # Kwarg for lab operator
#         )
#         self.assertTrue(result, "Requirement should be met for Glucose > 100.")

#     def test_evaluate_requirement_with_patient_lab_value_greater_than_failure(self):
#         """Test Requirement: Lab value (Glucose) is NOT greater than a specific value."""
#         requirement = Requirement.objects.create(
#             name="GlucoseGreaterThan100Fail", # Unique name
#             description="Glucose level must be greater than 100 mg/dL.",
#             numeric_value=100.0
#         )
#         requirement.requirement_types.add(self.rt_lab_value)
#         requirement.lab_values.add(self.lab_glucose)
#         requirement.operators.add(self.op_lab_greater_than)

#         patient_lab_value = PatientLabValue.objects.create(
#             patient=self.patient,
#             lab_value=self.lab_glucose,
#             value=90.0, 
#             unit=self.unit_mg_dl,
#             datetime=timezone.now()
#         )

#         result = requirement.evaluate(
#             patient_lab_value, 
#             mode="strict",
#             requirement_model=requirement,
#             patient_lab_values=[patient_lab_value]
#         )
#         self.assertFalse(result, "Requirement should NOT be met for Glucose <= 100.")

#     def test_evaluate_requirement_with_patient_examination_type_success(self):
#         """Test Requirement: PatientExamination is of a specific type (Colonoscopy)."""
#         requirement = Requirement.objects.create(
#             name="IsColonoscopy",
#             description="The examination must be a Colonoscopy."
#         )
#         requirement.requirement_types.add(self.rt_examination)
#         requirement.examinations.add(self.exam_colonoscopy) 
#         requirement.operators.add(self.op_match_all)

#         patient_exam = PatientExamination.objects.create(
#             patient=self.patient,
#             examination=self.exam_colonoscopy,
#             date_start=date(2023, 1, 15),
#             hash="test_exam_colo_success_req_eval" 
#         )

#         result = requirement.evaluate(patient_exam, mode="strict")
#         self.assertTrue(result, "Requirement should be met for Colonoscopy examination.")

#     def test_evaluate_requirement_with_patient_examination_type_failure(self):
#         """Test Requirement: PatientExamination is NOT of the specific type."""
#         requirement = Requirement.objects.create(
#             name="IsColonoscopyFail", # Unique name
#             description="The examination must be a Colonoscopy."
#         )
#         requirement.requirement_types.add(self.rt_examination)
#         requirement.examinations.add(self.exam_colonoscopy)
#         requirement.operators.add(self.op_match_all)

#         patient_exam = PatientExamination.objects.create(
#             patient=self.patient,
#             examination=self.exam_gastroscopy, # This is a Gastroscopy
#             date_start=date(2023, 2, 20),
#             hash="test_exam_gastro_fail_req_eval" 
#         )

#         result = requirement.evaluate(patient_exam, mode="strict")
#         self.assertFalse(result, "Requirement should NOT be met for Gastroscopy when Colonoscopy is required.")

#     def test_evaluate_requirement_with_patient_lab_sample_patient_has_disease_success(self):
#         """Test Req: Patient of LabSample has Diabetes. Uses PatientLabSample as input type."""
#         requirement = Requirement.objects.create(
#             name="PatientOfLabSampleHasDiabetes",
#             description="The patient associated with the lab sample must have Diabetes."
#         )
#         requirement.requirement_types.add(self.rt_lab_sample) # Evaluated based on PatientLabSample
#         requirement.diseases.add(self.disease_diabetes) # Condition on the patient
#         requirement.operators.add(self.op_match_all)

#         diabetic_patient = generate_patient(first_name="DiabeticPat", last_name="Sample", gender=self.gender_male, dob=date(1980,1,1))
#         PatientDisease.objects.create(patient=diabetic_patient, disease=self.disease_diabetes)
        
#         patient_lab_sample = PatientLabSample.objects.create(
#             patient=diabetic_patient,
#             sample_type=self.sample_type_blood,
#             date=timezone.now()
#         )
#         # PatientLabSample.links includes patients=[self.patient]
#         # Patient.links includes diseases=list(self.diseases.all())
#         # op_match_all should compare requirement.diseases with patient_lab_sample.patient.diseases

#         result = requirement.evaluate(patient_lab_sample, mode="strict")
#         self.assertTrue(result, "Requirement should be met if lab sample's patient has Diabetes.")

#     def test_evaluate_requirement_with_patient_lab_sample_patient_has_disease_failure(self):
#         """Test Req: Patient of LabSample does NOT have Diabetes. Uses PatientLabSample as input type."""
#         requirement = Requirement.objects.create(
#             name="PatientOfLabSampleHasDiabetesFail", # Unique name
#             description="The patient associated with the lab sample must have Diabetes."
#         )
#         requirement.requirement_types.add(self.rt_lab_sample)
#         requirement.diseases.add(self.disease_diabetes)
#         requirement.operators.add(self.op_match_all)

#         non_diabetic_patient = generate_patient(first_name="NonDiabeticPat", last_name="Sample", gender=self.gender_female, dob=date(1985,1,1))
#         # This patient does not have diabetes
        
#         patient_lab_sample = PatientLabSample.objects.create(
#             patient=non_diabetic_patient,
#             sample_type=self.sample_type_urine, # Sample type doesn't matter for this test logic
#             date=timezone.now()
#         )

#         result = requirement.evaluate(patient_lab_sample, mode="strict")
#         self.assertFalse(result, "Requirement should NOT be met if lab sample's patient does not have Diabetes.")

