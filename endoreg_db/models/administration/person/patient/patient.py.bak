import logging
import random
from datetime import date, datetime, timedelta
from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.db import models
from django.utils import timezone  # Add this import
from faker import Faker

from ..person import Person, PersonGenderValue

# Import ModelLinks and Disease for the links property

logger = logging.getLogger("patient")

if TYPE_CHECKING:
    from endoreg_db.models import AnonymExaminationReport, AnonymHistologyReport
    from endoreg_db.utils.links import ModelLinks

    from ....medical.patient.patient_disease import PatientDisease
    from ....medical.patient.patient_event import PatientEvent
    from ....medical.patient.patient_examination import PatientExamination
    from ....medical.patient.patient_lab_sample import PatientLabSample
    from ....medical.patient.patient_lab_sample import PatientLabSampleType
    from ....medical.patient.patient_lab_value import PatientLabValue
    from ....medical.patient.patient_medication import PatientMedication
    from ....media.pdf.raw_pdf import RawPdfFile
    from ....other.gender import Gender
    from ....medical.disease import Disease, DiseaseClassificationChoice
    from ....medical.medication.medication import Medication
    from ....medical.medication.medication_indication import MedicationIndication
    from ....medical.medication.medication_intake_time import MedicationIntakeTime
    from ...center.center import Center
    from .patient_external_id import PatientExternalID

NoPatientValue: TypeAlias = NoneType
PatientDateValue: TypeAlias = date | NoPatientValue
PatientTextValue: TypeAlias = str | NoPatientValue
PatientDateTimeValue: TypeAlias = datetime | NoPatientValue
PatientGenderInput: TypeAlias = "Gender | str | NoPatientValue"
PatientCenterInput: TypeAlias = "Center | str"
PatientLabSampleDate: TypeAlias = datetime | NoPatientValue


class _PatientGenderManager(Protocol):
    def resolve_by_name(self, name: str) -> "Gender | NoPatientValue": ...


class _PatientGenderSource(Protocol):
    name: str


class _PatientSaveSource(Protocol):
    def save(self) -> None: ...


class _PatientDiseaseLinkSource(Protocol):
    disease: "Disease | NoPatientValue"
    classification_choices: models.Manager["DiseaseClassificationChoice"]


class _PatientMedicationLinkSource(Protocol):
    medication: "Medication | NoPatientValue"
    medication_indication: "MedicationIndication | NoPatientValue"
    intake_times: models.Manager["MedicationIntakeTime"]


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

    first_name: models.CharField[str, str] = models.CharField(max_length=100)
    last_name: models.CharField[str, str] = models.CharField(max_length=100)
    dob: models.DateField[PatientDateValue, PatientDateValue] = models.DateField(
        null=True, blank=True
    )
    gender: models.ForeignKey[
        PersonGenderValue,
        PersonGenderValue,
    ] = models.ForeignKey("Gender", on_delete=models.SET_NULL, null=True, blank=True)
    center: models.ForeignKey[
        PatientCenterInput,
        PatientCenterInput,
    ] = models.ForeignKey("Center", on_delete=models.SET_NULL, null=True, blank=True)
    patient_hash: models.CharField[
        PatientTextValue,
        PatientTextValue,
    ] = models.CharField(max_length=255, blank=True, null=True)

    objects = cast(models.Manager["Patient"], models.Manager())

    if TYPE_CHECKING:
        center_id: int | None
        gender_id: int | None

        @property
        def events(self) -> models.Manager[PatientEvent]: ...

        @property
        def diseases(self) -> models.Manager[PatientDisease]: ...

        @property
        def patient_examinations(self) -> models.Manager[PatientExamination]: ...

        @property
        def anonymexaminationreport_set(
            self,
        ) -> models.Manager[AnonymExaminationReport]: ...

        @property
        def anonymhistologyreport_set(
            self,
        ) -> models.Manager[AnonymHistologyReport]: ...

        @property
        def external_ids(self) -> models.Manager[PatientExternalID]: ...

        @property
        def patientmedication_set(self) -> models.Manager[PatientMedication]: ...

        @property
        def lab_values(self) -> models.Manager[PatientLabValue]: ...

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} ({self.dob})"

    @classmethod
    def get_or_create_pseudo_patient_by_hash(
        cls,
        patient_hash: str,
        center: "Center | NoPatientValue" = None,
        gender: PatientGenderInput = None,
        birth_month: int | NoPatientValue = None,
        birth_year: int | NoPatientValue = None,
    ) -> tuple["Patient", bool]:
        from endoreg_db.utils import create_mock_patient_name, random_day_by_month_year

        from ....other import Gender  # Import Gender model

        created: bool = False

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

        # Ensure gender is a Gender model instance.
        if isinstance(gender, str):
            gender_manager = cast(_PatientGenderManager, Gender.objects)
            gender_obj = gender_manager.resolve_by_name(gender)
            if gender_obj is None:
                raise ValueError(f"Gender '{gender}' not found in database.")
        else:
            gender_obj = gender

        assert birth_month is not None
        if not 1 <= birth_month <= 12:
            raise ValueError("Birth month must be between 1 and 12.")
        assert birth_year is not None
        pseudo_dob = random_day_by_month_year(month=birth_month, year=birth_year)
        gender_source = cast(_PatientGenderSource, gender_obj)
        gender_name = gender_source.name
        first_name, last_name = create_mock_patient_name(gender_name)

        logger.info(f"Creating pseudo patient with hash {patient_hash}")
        logger.info(f"Generated name: {first_name} {last_name}")

        patient = cls.objects.create(
            first_name=first_name,
            last_name=last_name,
            dob=pseudo_dob,
            gender=gender_obj,  # Use the fetched/validated Gender instance.
            center=center,
            patient_hash=patient_hash,
            is_real_person=False,
        )

        cast(_PatientSaveSource, patient).save()
        created = True

        return patient, created

    def get_dob(self) -> PatientDateValue:
        return self.dob

    def get_patient_examinations(
        self,
    ) -> models.QuerySet["PatientExamination"]:  # field: self.patient_examinations
        """Returns all patient examinations for this patient ordered by date (most recent is first)."""
        return self.patient_examinations.order_by("-date_start")

    def create_examination(
        self,
        examination_name_str: PatientTextValue = None,
        date_start: PatientDateTimeValue = None,
        date_end: PatientDateTimeValue = None,
        save: bool = True,
    ) -> "PatientExamination":
        """Creates a patient examination for this patient."""
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
            patient_examination = PatientExamination(
                patient=self, date_start=date_start, date_end=date_end
            )

        if save:
            cast(_PatientSaveSource, patient_examination).save()

        return patient_examination

    def create_event(
        self,
        event_name_str: str,
        date_start: PatientDateTimeValue = None,
        date_end: PatientDateTimeValue = None,
        description: PatientTextValue = None,
    ) -> "PatientEvent":
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

    def create_examination_by_pdf(self, pdf: "RawPdfFile") -> "PatientExamination":
        """
        Creates a patient examination and associates it with the provided report report file.

        The examination is created for this patient, saved, and linked to the given RawPdfFile instance. The report's examination field is updated and saved. Returns the created examination instance.

        Args:
            pdf: The RawPdfFile to associate with the new examination.

        Returns:
            The created PatientExamination instance.
        """
        from ....medical import PatientExamination

        patient_examination = PatientExamination(patient=self)
        cast(_PatientSaveSource, patient_examination).save()
        pdf.examination = patient_examination
        cast(_PatientSaveSource, pdf).save()

        return patient_examination

    @classmethod
    def get_random_gender(cls, p_male: float = 0.5, p_female: float = 0.5) -> "Gender":
        """
        Get a Gender instance by name (male, female) from the database with given probability.

        :param p_male: Probability of selecting 'male' gender.
        :param p_female: Probability of selecting 'female' gender.
        :return: Gender instance selected based on given probabilities.
        """
        from ....other import Gender

        # Extract names and probabilities
        gender_names = ["male", "female"]
        probabilities = [p_male, p_female]

        selected_gender = random.choices(gender_names, probabilities)[0]

        # Fetch the corresponding Gender instance from the database.
        gender_manager = cast(_PatientGenderManager, Gender.objects)
        gender_obj = gender_manager.resolve_by_name(selected_gender)
        if gender_obj is None:
            raise ValueError(f"Gender '{selected_gender}' not found in database.")

        return gender_obj

    @classmethod
    def get_random_age(
        cls,
        min_age: int = 55,
        max_age: int = 90,
        mean_age: int = 65,
        std_age: int = 10,
        distribution: str = "normal",
    ) -> int:
        """
        Get a random age based on the given distribution.

        :param min_age: Minimum age.
        :param max_age: Maximum age.
        :param mean_age: Mean age.
        :param std_age: Standard deviation of the age.
        :param distribution: Distribution of the age.
        :return: Random age based on the given distribution.
        """
        min_age = int(min_age)
        max_age = int(max_age)
        if min_age > max_age:
            raise ValueError("min_age must be less than or equal to max_age.")
        if distribution == "normal":
            age = int(round(random.normalvariate(mean_age, std_age)))
            age = max(min_age, min(age, max_age))
        else:
            age = random.randint(min_age, max_age)

        return age

    @classmethod
    def get_dob_from_age(
        cls,
        age: int,
        current_date: date | datetime | NoPatientValue = None,
    ) -> date:
        """
        Get a date of birth based on the given age and current date.

        :param age: Age of the patient.
        :param current_date: Current date.
        :return: Date of birth based on the given age and current date.
        """
        age = int(age)
        if age < 0:
            raise ValueError("Age must be non-negative.")

        if current_date is None:
            current_date = timezone.now().date()
        elif isinstance(current_date, datetime):
            current_date = current_date.date()

        def _replace_year_safe(input_date: date, year: int) -> date:
            try:
                return input_date.replace(year=year)
            except ValueError:
                # Handle Feb 29 for non-leap target years.
                return input_date.replace(year=year, month=2, day=28)

        latest_dob = _replace_year_safe(current_date, current_date.year - age)
        earliest_dob = _replace_year_safe(
            current_date, current_date.year - age - 1
        ) + timedelta(days=1)
        offset_days = random.randint(0, (latest_dob - earliest_dob).days)

        return earliest_dob + timedelta(days=offset_days)

    @classmethod
    def get_random_name_for_gender(
        cls, gender_obj: "Gender", locale: str = "de_DE"
    ) -> tuple[str, str]:
        gender_source = cast(_PatientGenderSource, gender_obj)
        gender = gender_source.name
        fake = Faker(locale)

        if gender == "male":
            first_name = fake.first_name_male()
            last_name = fake.last_name_male()

        else:
            first_name = fake.first_name_female()
            last_name = fake.last_name_female()

        return last_name, first_name

    @classmethod
    def create_generic(
        cls, center: PatientCenterInput = "gplay_case_generator"
    ) -> "Patient":
        """
        Create a generic patient with random attributes.

        :param center: The center name or Center instance of the patient.
        :return: The created patient.
        """
        from ....administration import Center

        patient = cls()
        if patient.gender is None:
            gender = Patient.get_random_gender()
        else:
            gender = patient.gender
        last_name, first_name = Patient.get_random_name_for_gender(gender)

        if patient.dob is None:
            age = Patient.get_random_age()
        else:
            age = patient.age()
            assert age is not None, "Patient age is not set."
        dob = Patient.get_dob_from_age(age)

        # Fetch the center instance if a name is provided.
        if isinstance(center, str):
            center_obj = Center.objects.get(name=center)
        else:
            center_obj = center.objects.get(name=center.name)

        patient = Patient.objects.create(
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            gender=gender,
            center=center_obj,  # Assign the center instance.
        )
        # No need to call save() again after create()
        return patient

    @property
    def age_safe(self) -> int:
        age = self.age()
        assert age is not None, "Patient age is not set."
        return age

    def age(self) -> int | NoPatientValue:
        """
        Get the age of the patient.

        :return: The age of the patient.
        """
        # calculate correct age based on current date including day and month
        current_date = (
            timezone.now().date()
        )  # Use timezone.now() here too for consistency
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

    def create_lab_sample(
        self,
        sample_type: "PatientLabSampleType | str" = "generic",
        date: PatientLabSampleDate = None,
        save: bool = True,
    ) -> "PatientLabSample":
        """
        Create a lab sample for this patient.

        :param sample_type: The sample type. Should be either string of the sample types
            name or the sample type instance. If not set, the default sample type ("generic") is used.
        :param date: The date of the lab sample. Must be timezone-aware if provided.
        :return: The created lab sample.
        """
        from ....medical import PatientLabSample, PatientLabSampleType

        if date is None:
            date = timezone.now()  # Use timezone.now() instead of datetime.now()
        # Ensure the provided date is timezone-aware if it's not None
        elif timezone.is_naive(date):
            logger.warning(
                f"Received naive datetime {date} for PatientLabSample. Making it timezone-aware using current timezone."
            )
            date = timezone.make_aware(date, timezone.get_current_timezone())

        if isinstance(sample_type, str):
            sample_type = PatientLabSampleType.objects.get(name=sample_type)
            assert sample_type is not None, (
                f"Sample type with name '{sample_type}' not found."
            )

        patient_lab_sample = PatientLabSample.objects.create(
            patient=self, sample_type=sample_type, date=date
        )

        return patient_lab_sample

    @property
    def links(self) -> "ModelLinks":
        """
        Aggregates and returns all related model instances for linked-model traversal
        as ModelLinks. For a Patient, this includes their diseases, associated classification choices,
        all their lab values, and medication information.
        """
        from endoreg_db.models.medical.disease import (
            Disease,
            DiseaseClassificationChoice,
        )

        # Imports for medication related models
        from endoreg_db.models.medical.medication.medication import Medication
        from endoreg_db.models.medical.medication.medication_indication import (
            MedicationIndication,
        )
        from endoreg_db.models.medical.medication.medication_intake_time import (
            MedicationIntakeTime,
        )
        from endoreg_db.utils.links import ModelLinks

        # PatientMedication objects are retrieved via self.patientmedication_set
        # PatientLabValue objects are retrieved via self.lab_values

        patient_disease_instances = list(
            self.diseases.all()
        )  # These are PatientDisease model instances
        actual_diseases: list[Disease] = []
        all_classification_choices: list[DiseaseClassificationChoice] = []

        for pd_instance in patient_disease_instances:
            disease_source = cast(_PatientDiseaseLinkSource, pd_instance)
            if disease_source.disease:  # disease is a Disease instance
                actual_diseases.append(disease_source.disease)
            all_classification_choices.extend(
                list(disease_source.classification_choices.all())
            )

        # Assuming self.lab_values is a related manager for PatientLabValue instances
        patient_lab_value_instances = list(
            self.lab_values.all()
        )  # These are PatientLabValue model instances

        # Medication information
        # self.patientmedication_set gives a QuerySet of PatientMedication
        patient_medication_instances = list(self.patientmedication_set.all())

        actual_medications: list[Medication] = []
        med_indications: list[MedicationIndication] = []
        med_intake_times: list[MedicationIntakeTime] = []

        for pm_instance in patient_medication_instances:
            medication_source = cast(_PatientMedicationLinkSource, pm_instance)
            if medication_source.medication:
                actual_medications.append(medication_source.medication)
            if medication_source.medication_indication:
                med_indications.append(medication_source.medication_indication)
            med_intake_times.extend(
                list(medication_source.intake_times.all())
            )  # pm_instance.intake_times is a ManyRelatedManager for MedicationIntakeTime

        return ModelLinks.model_validate(
            {
                "diseases": list(set(actual_diseases)),
                "patient_diseases": patient_disease_instances,
                "disease_classification_choices": list(set(all_classification_choices)),
                "patient_lab_values": patient_lab_value_instances,
                "medications": list(set(actual_medications)),
                "patient_medications": patient_medication_instances,
                "medication_indications": list(set(med_indications)),
                "medication_intake_times": list(set(med_intake_times)),
            }
        )
