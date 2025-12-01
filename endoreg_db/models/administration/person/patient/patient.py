import logging
import random
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional  # Added List

from django.db import models
from django.utils import timezone  # Add this import
from faker import Faker

from ..person import Person

# Import RequirementLinks and Disease for the links property

logger = logging.getLogger("patient")

if TYPE_CHECKING:
    from endoreg_db.models import (
        AnonymExaminationReport,
        AnonymHistologyReport,
        Center,
        ExaminationIndication,
        Gender,
        PatientDisease,
        PatientEvent,
        PatientExamination,
        PatientExternalID,
        PatientLabValue,
        PatientMedication,
        RawPdfFile,
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

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    dob = models.DateField(null=True, blank=True)
    gender = models.ForeignKey("Gender", on_delete=models.SET_NULL, null=True, blank=True)
    center = models.ForeignKey("Center", on_delete=models.SET_NULL, null=True, blank=True)
    patient_hash = models.CharField(max_length=255, blank=True, null=True)

    objects = models.Manager()  # Default manager

    if TYPE_CHECKING:
        from django.db.models.manager import RelatedManager

        first_name: models.CharField[str]
        last_name: models.CharField[str]
        dob: models.DateField[date | None]
        gender: models.ForeignKey["Gender | None"]
        center: models.ForeignKey["Center | None"]

        @property
        def events(self) -> RelatedManager[PatientEvent]: ...

        @property
        def diseases(self) -> RelatedManager[PatientDisease]: ...

        @property
        def patient_examinations(self) -> RelatedManager[PatientExamination]: """
Access the manager for this patient's examinations.

@returns RelatedManager[PatientExamination]: The related manager for PatientExamination instances linked to this patient, supporting queryset operations such as filtering, ordering, and creation.
"""
...

        @property
        def anonymexaminationreport_set(self) -> RelatedManager[AnonymExaminationReport]: """
Related manager for anonymized examination reports linked to this patient.

Returns:
    RelatedManager[AnonymExaminationReport]: Manager providing access to AnonymExaminationReport instances associated with the patient.
"""
...

        @property
        def anonymhistologyreport_set(self) -> RelatedManager[AnonymHistologyReport]: """
Django related manager exposing this patient's anonymized histology reports.

Returns:
    RelatedManager[AnonymHistologyReport]: Manager for AnonymHistologyReport instances associated with the patient.
"""
...

        @property
        def external_ids(self) -> RelatedManager[PatientExternalID]: """
Access the related manager for this patient's external identifier records.

Provides a RelatedManager yielding PatientExternalID instances associated with the patient.
Returns:
    RelatedManager[PatientExternalID]: Manager for the patient's external identifier objects.
"""
...

        @property
        def patientmedication_set(self) -> RelatedManager[PatientMedication]: ...

        @property
        def lab_values(self) -> RelatedManager[PatientLabValue]: ...

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.dob})"

    @classmethod
    def get_or_create_pseudo_patient_by_hash(
        cls,
        patient_hash: str,
        center: Optional["Center"] = None,
        gender: Optional["Gender | str"] = None,  # Allow string type hint
        birth_month: Optional[int] = None,
        birth_year: Optional[int] = None,
    ):
        """
        Retrieve a patient by hash or create a pseudo patient with generated attributes when none exists.
        
        Parameters:
            patient_hash (str): Unique hash identifying the patient to find or create.
            center (Optional[Center]): Center object or center name used for creating a new pseudo patient.
            gender (Optional[Gender | str]): Gender object or gender name to assign to a newly created pseudo patient.
            birth_month (Optional[int]): Month (1-12) used to generate the pseudo date of birth for a new patient.
            birth_year (Optional[int]): Year used to generate the pseudo date of birth for a new patient.
        
        Returns:
            tuple: (patient, created) where `patient` is the found or newly created Patient instance and
            `created` is `True` if a new pseudo patient was created, `False` if an existing patient was returned.
        
        Raises:
            AssertionError: If creating a new patient and any of `center`, `gender`, `birth_month`, or `birth_year` is not provided.
            ValueError: If `gender` is neither a string nor a Gender instance.
        """
        from endoreg_db.utils import create_mock_patient_name, random_day_by_year

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
        assert birth_month, "Birth month must be provided to create a new pseudo patient"
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

    def get_dob(self) -> date | None:
        return self.dob

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
        """
        Create a PatientExamination linked to this patient.
        
        Creates a PatientExamination optionally associated with an existing Examination (by name), sets the provided start and end datetimes, and optionally saves the record to the database.
        
        Parameters:
            examination_name_str (Optional[str]): Name of an existing Examination to associate; if omitted, an unnamed PatientExamination is created.
            date_start (Optional[datetime.datetime]): Start datetime for the examination.
            date_end (Optional[datetime.datetime]): End datetime for the examination.
            save (bool): If True, persist the created PatientExamination to the database.
        
        Returns:
            PatientExamination: The created PatientExamination instance.
        """
        from ....medical import Examination, PatientExamination

        if examination_name_str:
            examination = Examination.objects.get(name=examination_name_str)
            patient_examination = PatientExamination(
                patient=self,
                examination=examination,
                date_start=date_start,
                date_end=date_end,
            )

        else:
            patient_examination = PatientExamination(patient=self, date_start=date_start, date_end=date_end)

        if save:
            patient_examination.save()

        return patient_examination

    def create_event(
        self,
        event_name_str: str,
        date_start: Optional[datetime] = None,
        date_end: Optional[datetime] = None,
        description: Optional[str] = None,
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
        Create a PatientExamination for this patient and link the given RawPdfFile to it.
        
        The new PatientExamination is saved, the pdf.examination field is set to that examination and the pdf is saved.
        
        Parameters:
            pdf (RawPdfFile): The PDF file to associate with the created examination.
        
        Returns:
            PatientExamination: The created PatientExamination instance.
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
        Selects a Gender model instance by sampling 'male' or 'female' according to the provided weights.
        
        Parameters:
            p_male (float): Relative weight for selecting "male".
            p_female (float): Relative weight for selecting "female".
        
        Returns:
            Gender: The Gender instance whose name was sampled ("male" or "female").
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
    def get_random_age(cls, min_age=55, max_age=90, mean_age=65, std_age=10, distribution="normal"):
        """
        Sample an integer age according to the specified distribution.
        
        Parameters:
        	min_age (int): Minimum age used when sampling uniformly.
        	max_age (int): Maximum age used when sampling uniformly.
        	mean_age (float): Mean used when sampling from a normal distribution.
        	std_age (float): Standard deviation used when sampling from a normal distribution.
        	distribution (str): If "normal", sample from a normal distribution using mean_age and std_age; otherwise sample uniformly between min_age and max_age.
        
        Returns:
        	int: An age sampled according to the chosen distribution.
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
            center=center_obj,  # Assign the center object
        )
        # No need to call save() again after create()
        return patient

    @property
    def age_safe(self) -> int:
        age = self.age()
        assert age is not None, "Patient age is not set."
        return age

    def age(self) -> int | None:
        """
        Compute the patient's age in completed years using the current timezone-aware date.
        
        Returns:
            The patient's age in years (integer), or `None` if the patient's date of birth is not set.
        """
        # calculate correct age based on current date including day and month
        current_date = timezone.now().date()  # Use timezone.now() here too for consistency
        dob = self.dob
        # Ensure dob is not None before calculation
        if dob:
            age = current_date.year - dob.year - ((current_date.month, current_date.day) < (dob.month, dob.day))
            return age
        return None  # Or handle the case where dob is None appropriately

    def create_lab_sample(self, sample_type="generic", date=None, save=True):
        """
        Create and persist a lab sample for this patient.
        
        Parameters:
            sample_type (str | PatientLabSampleType): The sample type to assign. Provide either the name of an existing PatientLabSampleType or a PatientLabSampleType instance. Defaults to "generic".
            date (datetime | None): Timestamp for the sample. If None, uses the current timezone-aware time. If a naive datetime is provided, it will be converted to the current timezone.
            save (bool): Ignored; the created sample is persisted before being returned (parameter kept for compatibility).
        
        Returns:
            PatientLabSample: The created and saved PatientLabSample instance.
        
        Raises:
            ValueError: If sample_type is neither a string nor a PatientLabSampleType instance.
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
            assert sample_type is not None, f"Sample type with name '{sample_type}' not found."
        elif not isinstance(sample_type, PatientLabSampleType):
            raise ValueError("Sample type must be either a string or a PatientLabSampleType object.")

        patient_lab_sample = PatientLabSample.objects.create(patient=self, sample_type=sample_type, date=date)

        return patient_lab_sample

    @property
    def links(self) -> "RequirementLinks":
        """
        Collects related patient data into a RequirementLinks object for requirement evaluation.
        
        Gathers the patient's disease records and their referenced Disease instances, disease classification choices,
        all patient lab values, patient medication records and their referenced Medication instances, medication
        indications, and medication intake times, and packages them into a RequirementLinks instance.
        
        Returns:
            RequirementLinks: An object with the following populated fields:
                - diseases: unique list of Disease instances referenced by the patient's disease records
                - patient_diseases: list of PatientDisease instances for this patient
                - disease_classification_choices: unique list of DiseaseClassificationChoice instances from patient diseases
                - patient_lab_values: list of PatientLabValue instances for this patient
                - medications: unique list of Medication instances referenced by patient medications
                - patient_medications: list of PatientMedication instances for this patient
                - medication_indications: unique list of MedicationIndication instances from patient medications
                - medication_intake_times: unique list of MedicationIntakeTime instances associated with patient medications
        """
        from endoreg_db.models.medical.disease import Disease, DiseaseClassificationChoice

        # Imports for medication related models
        from endoreg_db.models.medical.medication.medication import Medication
        from endoreg_db.models.medical.medication.medication_indication import MedicationIndication
        from endoreg_db.models.medical.medication.medication_intake_time import MedicationIntakeTime
        from endoreg_db.utils.links.requirement_link import RequirementLinks
        # PatientMedication objects are retrieved via self.patientmedication_set
        # PatientLabValue objects are retrieved via self.lab_values

        patient_disease_instances = list(self.diseases.all())  # These are PatientDisease model instances
        actual_diseases: List[Disease] = []
        all_classification_choices: List[DiseaseClassificationChoice] = []

        for pd_instance in patient_disease_instances:
            if pd_instance.disease:  # pd_instance.disease is a Disease instance
                actual_diseases.append(pd_instance.disease)
            all_classification_choices.extend(list(pd_instance.classification_choices.all()))

        # Assuming self.lab_values is a related manager for PatientLabValue instances
        patient_lab_value_instances = list(self.lab_values.all())  # These are PatientLabValue model instances

        # Medication information
        # self.patientmedication_set gives a QuerySet of PatientMedication
        patient_medication_instances = list(self.patientmedication_set.all())

        actual_medications: List[Medication] = []
        med_indications: List[MedicationIndication] = []
        med_intake_times: List[MedicationIntakeTime] = []

        for pm_instance in patient_medication_instances:
            if pm_instance.medication:  # pm_instance.medication is a Medication instance
                actual_medications.append(pm_instance.medication)
            if pm_instance.medication_indication:  # pm_instance.medication_indication is a MedicationIndication instance
                med_indications.append(pm_instance.medication_indication)
            med_intake_times.extend(list(pm_instance.intake_times.all()))  # pm_instance.intake_times is a ManyRelatedManager for MedicationIntakeTime

        return RequirementLinks(
            diseases=list(set(actual_diseases)),
            patient_diseases=patient_disease_instances,
            disease_classification_choices=list(set(all_classification_choices)),
            patient_lab_values=patient_lab_value_instances,
            medications=list(set(actual_medications)),
            patient_medications=patient_medication_instances,
            medication_indications=list(set(med_indications)),
            medication_intake_times=list(set(med_intake_times)),
        )