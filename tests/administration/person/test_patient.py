from django.test import TestCase
from endoreg_db.models import (
    Patient, Examination, PatientExamination,
    ExaminationIndication,
    PatientExaminationIndication,
    Center, # Import Center
    PatientLabSample,
)
from datetime import date
from logging import getLogger
from pathlib import Path
import random


logger = getLogger(__name__)

logger.debug("Starting test for Patient model")

from ...helpers.data_loader import (
    load_unit_data,
    load_distribution_data,
    load_center_data,
    load_examination_data,
    load_examination_indication_data,
    load_gender_data,
    load_lab_value_data
)

from ...helpers.default_objects import (
    generate_patient,
    get_random_default_examination,
    get_random_default_examination_indication,
    get_default_egd_pdf,
    get_random_gender,
)

class PatientModelTest(TestCase):
    def setUp(self):
        load_center_data()
        load_gender_data()
        self.patient = generate_patient(
            first_name="John",
            last_name="Doe",
            birth_date="1990-01-01",
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
        # Ensure the default center for create_generic exists
        Center.objects.get_or_create(name="gplay_case_generator")
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
        
    def test_examination_by_pdf_creation(self):
        """Test if the examination by pdf is created correctly."""

        # create a pdf file
        sample_pdf = get_default_egd_pdf()
        sample_examination_3 = self.patient.create_examination_by_pdf(
            sample_pdf
        )

        self.assertIsInstance(sample_examination_3, PatientExamination)

        # make sure the pdf file exists
        files = sample_examination_3.raw_pdf_files.all()
        self.assertEqual(len(files), 1)
        file_exists = Path(files[0].file.path).exists()
        self.assertEqual(file_exists, True)

    def test_get_random_age(self):
        """Test if the get_random_age method returns a valid age."""
        age = Patient.get_random_age()
        self.assertIsInstance(age, int)
        self.assertGreaterEqual(age, 0)
        self.assertLessEqual(age, 100)

    def test_get_random_dob(self):
        center = self.patient.center

        age = Patient.get_random_age()
        dob = Patient.get_dob_from_age(age)

        # create a patient object
        patient = Patient.objects.create(
            first_name="John",
            last_name="Doe",
            dob=dob,
            center=center,
        )
        patient_age = patient.age()
        self.assertEqual(patient_age, age)

    def test_create_generic(self):
        """Test if the create_generic method creates a patient with a random name and dob."""
        patient = Patient.create_generic()
        self.assertIsInstance(patient, Patient)
        self.assertIsNotNone(patient.first_name)
        self.assertIsNotNone(patient.last_name)
        self.assertIsNotNone(patient.dob)
        # Assert the correct center is assigned
        self.assertIsNotNone(patient.center)
        self.assertEqual(patient.center.name, "gplay_case_generator")

    def test_get_or_create_pseudo_patient_by_hash(self):
        """Test if the get_or_create_pseudo_patient_by_hash method creates a patient with a random name and dob."""
        center = self.patient.center

        gender = get_random_gender()
        patient_hash = "test_hash"
        birth_year = random.randint(1950, 2000)
        birth_month = random.randint(1, 12)
        patient, created = Patient.get_or_create_pseudo_patient_by_hash(
            patient_hash=patient_hash,
            center=center,
            gender = gender,
            birth_month=birth_month,
            birth_year=birth_year,
        )
        self.assertIsInstance(patient, Patient)
        self.assertIsNotNone(patient.first_name)
        self.assertIsNotNone(patient.last_name)
        self.assertIsNotNone(patient.dob)
        self.assertEqual(patient.patient_hash, patient_hash)
        self.assertEqual(created, True)

        # make sure the patient is not created a second time
        patient_2, created_2 = Patient.get_or_create_pseudo_patient_by_hash(patient_hash)
        self.assertIsInstance(patient_2, Patient)
        self.assertEqual(patient_2, patient)
        self.assertEqual(created_2, False)
        
    def test_create_lab_sample(self):
        """Test if the create_lab_sample method creates a lab sample with a random name and dob."""
        load_unit_data()
        load_distribution_data()
        load_lab_value_data()

        # create a lab sample
        lab_sample = self.patient.create_lab_sample(
            sample_type="generic",
        )
        self.assertIsInstance(lab_sample, PatientLabSample)
        self.assertEqual(lab_sample.sample_type.name, "generic")
        self.assertEqual(lab_sample.patient, self.patient)
    
    # After each test, we need to make sure that we delete the RawPdfObject
    # def tearDown(self):
    #     self.sample_pdf.delete()

