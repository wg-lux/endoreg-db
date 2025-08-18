from ..person import Person
from django.db import models
from faker import Faker
import random
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional  # Added List
import logging
from django.utils import timezone  # Add this import

# Import RequirementLinks and Disease for the links property

logger = logging.getLogger("patient")

if TYPE_CHECKING:
    from endoreg_db.models import (
        ExaminationIndication,
        PatientEvent, PatientDisease,
        Gender,
        PatientExamination,
        Center,
        AnonymExaminationReport,
        AnonymHistologyReport, RawPdfFile,
        # Added for links property
        Medication,
        PatientMedication,
        MedicationIndication,
        MedicationIntakeTime,
        PatientLabValue, # Assuming self.lab_values are PatientLabValue instances
        LabValue # If RequirementLinks expects actual LabValue instances
    )
    from endoreg_db.utils.links.requirement_link import RequirementLinks

class Patient(Person):
    """
    A class representing a patient.

    Attributes inhereted from Person:
        first_name (str): The first name of the patient.
        last_name (str): The last name of the patient.
        dob (datetime.date): The date of birth of the patient.
        gender (Foreign Key): The gender of the patient.
        email (str): The email address of the patient.
        phone (str): The phone number of the patient.

    """

    # -----gc-08-dev--changings---
    first_name = models.CharField(max_length=100) # type: ignore[assignment]
    last_name = models.CharField(max_length=100) # type: ignore[assignment]
    dob = models.DateField(null=True, blank=True) # type: ignore[assignment]
    gender = models.ForeignKey( # type: ignore[assignment]
        "Gender", on_delete=models.SET_NULL, null=True, blank=True
    )
    center = models.ForeignKey( # type: ignore[assignment]
        "Center", on_delete=models.SET_NULL, null=True, blank=True
    )
    patient_hash = models.CharField(max_length=255, blank=True, null=True)
    
    objects = models.Manager()  # Default manager

    if TYPE_CHECKING:
        first_name: str
        last_name: str
        dob: datetime.date
        gender: "Gender"
        center: "Center"
        events: models.QuerySet["PatientEvent"]
        diseases: models.QuerySet["PatientDisease"]
        patient_examinations: models.QuerySet["PatientExamination"]
        anonymexaminationreport_set: models.QuerySet["AnonymExaminationReport"]
        anonymhistologyreport_set: models.QuerySet["AnonymHistologyReport"]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.dob})"

    @classmethod
    def get_or_create_pseudo_patient_by_hash(
        cls,
        patient_hash: str,
        center: "Center" = None,
        gender: "Gender | str" = None,  # Allow string type hint
        birth_month: int = None,
        birth_year: int = None,
    ):
        from endoreg_db.utils import random_day_by_year, create_mock_patient_name
        from ....other import Gender  # Import Gender model

        created = False

        existing_patient = cls.objects.filter(patient_hash=patient_hash).first()
        if existing_patient:
            logger.info(f"Patient with hash {patient_hash} already exists")
            logger.info(f"Returning existing patient: {existing_patient}")
            return existing_patient, created

        # If no patient with the given hash exists, create a new pseudo patient
        assert center, "Center must be provided to create a new pseudo patient"
        assert gender, "Gender must be provided to create a new pseudo patient"
        assert birth_month, (
            "Birth month must be provided to create a new pseudo patient"
        )
        assert birth_year, "Birth year must be provided to create a new pseudo patient"

        # Ensure gender is a Gender object
        if isinstance(gender, str):
            gender_obj = Gender.objects.get(name=gender)
        elif isinstance(gender, Gender):
            gender_obj = gender
        else:
            raise ValueError("Gender must be a string name or a Gender object.")

        pseudo_dob = random_day_by_year(birth_year)
        gender_name = gender_obj.name
        first_name, last_name = create_mock_patient_name(gender_name)

        logger.info(f"Creating pseudo patient with hash {patient_hash}")
        logger.info(f"Generated name: {first_name} {last_name}")

        patient = cls.objects.create(
            first_name=first_name,
            last_name=last_name,
            dob=pseudo_dob,
            gender=gender_obj,  # Use the fetched/validated Gender object
            patient_hash=patient_hash,
            is_real_person=False,
        )

        patient.save()
        created = True

        return patient, created

    def get_dob(self) -> datetime.date:
        dob: datetime.date = self.dob
        return dob

    def get_patient_examinations(self):  # field: self.patient_examinations
        """Returns all patient examinations for this patient ordered by date (most recent is first)."""
        return self.patient_examinations.order_by("-date_start")

    def create_examination(
        self,
        examination_name_str: Optional[str] = None,
        date_start: Optional[datetime] = None,
        date_end: Optional[datetime] = None,
        save: bool = True,
    ) -> "PatientExamination":
        """Creates a patient examination for this patient."""

        if examination_name_str:
            from ....medical import Examination, PatientExamination

            examination = Examination.objects.get(name=examination_name_str)
            patient_examination = PatientExamination(
                patient=self,
                examination=examination,
                date_start=date_start,
                date_end=date_end,
            )

        else:
            patient_examination = PatientExamination(
                patient=self, date_start=date_start, date_end=date_end
            )

        if save:
            patient_examination.save()

        return patient_examination

    def create_examination_by_indication(
        self, indication: "ExaminationIndication", date_start: datetime = None, date_end: datetime = None
    ):
        from ....medical import (
            PatientExaminationIndication,
            PatientExamination,
        )

        examination = indication.get_examination()

        patient_examination = PatientExamination.objects.create(
            patient=self,
            examination=examination,
            date_start=date_start,
            date_end=date_end,
        )

        patient_examination.save()

        patient_examination_indication = PatientExaminationIndication.objects.create(
            patient_examination=patient_examination, examination_indication=indication
        )
        patient_examination_indication.save()

        return patient_examination, patient_examination_indication

    def create_event(
        self,
        event_name_str: str,
        date_start: datetime = None,
        date_end: datetime = None,
        description: str = None,
    ):
        """
        Creates a patient event with the specified event name and start date.
        
        If no start date is provided, the current datetime is used. Returns the created PatientEvent instance.
        """
        from ....medical import Event, PatientEvent

        event = Event.objects.get(name=event_name_str)

        if not date_start:
            date_start = datetime.now()

        patient_event = PatientEvent.objects.create(
            patient=self,
            event=event,
            date_start=date_start,
        )

        return patient_event

    def create_examination_by_pdf(self, pdf: "RawPdfFile"):
        """
        Creates a patient examination and associates it with the provided PDF report file.
        
        The examination is created for this patient, saved, and linked to the given RawPdfFile instance. The PDF's examination field is updated and saved. Returns the created examination instance.
        
        Args:
            pdf: The RawPdfFile to associate with the new examination.
        
        Returns:
            The created PatientExamination instance.
        """
        from ....medical import PatientExamination
        patient_examination = PatientExamination(patient=self)
        patient_examination.save()
        pdf.examination = patient_examination
        pdf.save()

        return patient_examination

    @classmethod
    def get_random_gender(cls, p_male=0.5, p_female=0.5):
        """
        Get a Gender object by name (male, female) from the database with given probability.

        :param p_male: Probability of selecting 'male' gender.
        :param p_female: Probability of selecting 'female' gender.
        :return: Gender object selected based on given probabilities.
        """
        from ....other import Gender

        # Extract names and probabilities
        gender_names = ["male", "female"]
        probabilities = [p_male, p_female]

        selected_gender = random.choices(gender_names, probabilities)[0]

        # Fetch the corresponding Gender object from the database
        gender_obj = Gender.objects.get(name=selected_gender)

        return gender_obj

    @classmethod
    def get_random_age(
        cls, min_age=55, max_age=90, mean_age=65, std_age=10, distribution="normal"
    ):
        """
        Get a random age based on the given distribution.

        :param min_age: Minimum age.
        :param max_age: Maximum age.
        :param mean_age: Mean age.
        :param std_age: Standard deviation of the age.
        :param distribution: Distribution of the age.
        :return: Random age based on the given distribution.
        """
        if distribution == "normal":
            age = int(random.normalvariate(mean_age, std_age))
        else:
            age = int(random.uniform(min_age, max_age))

        return age

    @classmethod
    def get_dob_from_age(cls, age, current_date=None):
        """
        Get a date of birth based on the given age and current date.

        :param age: Age of the patient.
        :param current_date: Current date.
        :return: Date of birth based on the given age and current date.
        """
        if current_date is None:
            current_date = datetime.now()
        dob = current_date.replace(year=current_date.year - age).date()

        # TODO
        # randomize the day and month by adding a random number of days (0-364) to the date

        return dob

    @classmethod
    def get_random_name_for_gender(cls, gender_obj, locale="de_DE"):
        gender = gender_obj.name
        fake = Faker(locale)

        if gender == "male":
            first_name = fake.first_name_male()
            last_name = fake.last_name_male()

        else:
            first_name = fake.first_name_female()
            last_name = fake.last_name_female()

        return last_name, first_name

    @classmethod
    def create_generic(cls, center="gplay_case_generator"):
        """
        Create a generic patient with random attributes.

        :param center: The center name or Center object of the patient.
        :return: The created patient.
        """
        from ....administration import Center

        gender = Patient.get_random_gender()
        last_name, first_name = Patient.get_random_name_for_gender(gender)

        age = Patient.get_random_age()
        dob = Patient.get_dob_from_age(age)

        # Fetch the center object if a name is provided
        if isinstance(center, str):
            center_obj = Center.objects.get(name=center)
        elif isinstance(center, Center):
            center_obj = center
        else:
            raise ValueError("Center must be a string name or a Center object.")

        patient = Patient.objects.create(
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            gender=gender,
            center=center_obj, # Assign the center object
        )
        # No need to call save() again after create()
        return patient

    def age(self) -> int | None:
        """
        Get the age of the patient.

        :return: The age of the patient.
        """
        # calculate correct age based on current date including day and month
        current_date = timezone.now().date()  # Use timezone.now() here too for consistency
        dob = self.dob
        # Ensure dob is not None before calculation
        if dob:
            age = (
                current_date.year
                - dob.year
                - ((current_date.month, current_date.day) < (dob.month, dob.day))
            )
            return age
        return None  # Or handle the case where dob is None appropriately

    def create_lab_sample(self, sample_type="generic", date=None, save=True):
        """
        Create a lab sample for this patient.

        :param sample_type: The sample type. Should be either string of the sample types
            name or the sample type object. If not set, the default sample type ("generic") is used.
        :param date: The date of the lab sample. Must be timezone-aware if provided.
        :return: The created lab sample.
        """
        from ....medical import PatientLabSample, PatientLabSampleType

        if date is None:
            date = timezone.now()  # Use timezone.now() instead of datetime.now()
        # Ensure the provided date is timezone-aware if it's not None
        elif timezone.is_naive(date):
            logger.warning(f"Received naive datetime {date} for PatientLabSample. Making it timezone-aware using current timezone.")
            date = timezone.make_aware(date, timezone.get_current_timezone())

        if isinstance(sample_type, str):
            sample_type = PatientLabSampleType.objects.get(name=sample_type)
            assert sample_type is not None, (
                f"Sample type with name '{sample_type}' not found."
            )
        elif not isinstance(sample_type, PatientLabSampleType):
            raise ValueError(
                "Sample type must be either a string or a PatientLabSampleType object."
            )

        patient_lab_sample = PatientLabSample.objects.create(
            patient=self, sample_type=sample_type, date=date
        )

        return patient_lab_sample

    @property
    def links(self) -> "RequirementLinks":
        """
        Aggregates and returns all related model instances relevant for requirement evaluation
        as a RequirementLinks object. For a Patient, this includes their diseases, associated classification choices,
        all their lab values, and medication information.
        """
        from endoreg_db.utils.links.requirement_link import RequirementLinks
        from endoreg_db.models.medical.disease import Disease, DiseaseClassificationChoice
        
        # Imports for medication related models
        from endoreg_db.models.medical.medication.medication import Medication
        from endoreg_db.models.medical.medication.medication_indication import MedicationIndication
        from endoreg_db.models.medical.medication.medication_intake_time import MedicationIntakeTime
        # PatientMedication objects are retrieved via self.patientmedication_set
        # PatientLabValue objects are retrieved via self.lab_values

        patient_disease_instances = list(self.diseases.all()) # These are PatientDisease model instances
        actual_diseases: List[Disease] = []
        all_classification_choices: List[DiseaseClassificationChoice] = []

        for pd_instance in patient_disease_instances:
            if pd_instance.disease: # pd_instance.disease is a Disease instance
                actual_diseases.append(pd_instance.disease)
            all_classification_choices.extend(list(pd_instance.classification_choices.all()))
        
        # Assuming self.lab_values is a related manager for PatientLabValue instances
        patient_lab_value_instances = list(self.lab_values.all()) # These are PatientLabValue model instances

        # Medication information
        # self.patientmedication_set gives a QuerySet of PatientMedication
        patient_medication_instances = list(self.patientmedication_set.all()) 
        
        actual_medications: List[Medication] = []
        med_indications: List[MedicationIndication] = []
        med_intake_times: List[MedicationIntakeTime] = []

        for pm_instance in patient_medication_instances:
            if pm_instance.medication: # pm_instance.medication is a Medication instance
                actual_medications.append(pm_instance.medication)
            if pm_instance.medication_indication: # pm_instance.medication_indication is a MedicationIndication instance
                med_indications.append(pm_instance.medication_indication)
            med_intake_times.extend(list(pm_instance.intake_times.all())) # pm_instance.intake_times is a ManyRelatedManager for MedicationIntakeTime

        return RequirementLinks(
            diseases=list(set(actual_diseases)), 
            patient_diseases=patient_disease_instances, 
            disease_classification_choices=list(set(all_classification_choices)), 
            patient_lab_values=patient_lab_value_instances,
            medications=list(set(actual_medications)), 
            patient_medications=patient_medication_instances,
            medication_indications=list(set(med_indications)),
            medication_intake_times=list(set(med_intake_times))
        )
