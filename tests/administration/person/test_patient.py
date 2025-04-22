from django.test import TestCase
from endoreg_db.models import (
    Patient, Examination, PatientExamination,
    ExaminationIndication,
    PatientExaminationIndication
)

from ...helpers.data_loader import (
    load_center_data,
    load_examination_data,
    load_examination_indication_data,
    load_gender_data
)

from ...helpers.default_objects import (
    generate_patient,
    get_random_default_examination,
    get_random_default_examination_indication,
)

class PatientModelTest(TestCase):
    def setUp(self):
        load_center_data()
        load_gender_data()
        self.patient = generate_patient(
            first_name="John",
            last_name="Doe",
            birth_date="1990-01-01",
            center="university_hospital_wuerzburg",
        )
        self.patient.save()

    def test_patient_creation(self):
        """Test if the patient is created correctly."""
        self.assertIsInstance(self.patient, Patient)
        self.assertEqual(self.patient.first_name, self.patient.first_name)
        self.assertEqual(self.patient.last_name, self.patient.last_name)
        self.assertEqual(self.patient.dob, self.patient.dob)
        self.assertEqual(self.patient.center, self.patient.center)

    def test_get_dob(self):
        """Test if the get_dob method returns the correct date of birth."""
        dob = self.patient.get_dob()
        self.assertEqual(dob, self.patient.dob)

class PatientModelWithExaminationTest(TestCase):
    def setUp(self):
        load_center_data()
        load_gender_data()
        load_examination_data()
        load_examination_indication_data()

        self.patient = generate_patient(
            gender="male",
            center="university_hospital_wuerzburg",
        )
        self.patient.save()

        self.sample_examination_object = get_random_default_examination()
        self.patient_examination = self.patient.create_examination(
            examination_name_str = self.sample_examination_object.name,
        )
        examination_indication = get_random_default_examination_indication()
        self.patient_examination_2, self.examination_2_indication = self.patient.create_examination_by_indication(
            indication=examination_indication,
        )


    def test_examination_creation(self):
        """Test if the examination is created correctly."""
        self.assertIsInstance(self.patient_examination, PatientExamination)
        self.assertEqual(self.patient_examination.examination, self.sample_examination_object)
        self.assertEqual(self.patient_examination.patient, self.patient)

    def test_get_patient_examinations(self):
        """Test if the get_patient_examinations method returns the correct examinations."""
        examinations = self.patient.get_patient_examinations()
        self.assertIn(self.patient_examination, examinations)

    def test_examination_by_indication_creation(self):
        """Test if the examination by indication is created correctly."""
        self.assertIsInstance(self.examination_2_indication, PatientExaminationIndication)
        self.assertEqual(self.examination_2_indication.get_examination(), self.patient_examination_2.examination)
        self.assertEqual(self.examination_2_indication.get_patient_examination(), self.patient_examination_2)
        self.assertEqual(self.examination_2_indication.get_patient(), self.patient_examination.patient)
        
    # def test_create_examination_by_pdf(self):
    #     """Test if the create_examination_by_pdf method creates the examination correctly."""
    #     examination = self.patient.create_examination_by_pdf(
    #         pdf_path="path/to/pdf",
    #         examination_name_str=self.sample_examination_object.name,
    #     )
    #     self.assertIsInstance(examination, PatientExamination)
    #     self.assertEqual(examination.examination, self.sample_examination_object)
    #     self.assertEqual(examination.patient, self.patient)
