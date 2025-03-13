from ..person import Person
from django import forms
from django.forms import DateInput
from ...patient import PatientExamination
from ...data_file import ReportFile
from django.db import models
from faker import Faker
import random
from datetime import datetime
from icecream import ic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import Center, Gender


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
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    dob = models.DateField(null=True, blank=True)
    gender = models.ForeignKey(
        "Gender", on_delete=models.SET_NULL, null=True, blank=True
    )
    center = models.ForeignKey(
        "Center", on_delete=models.SET_NULL, null=True, blank=True
    )
    patient_hash = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.dob})"

    @classmethod
    def get_or_create_pseudo_patient_by_hash(
        cls,
        patient_hash: str,
        center: "Center" = None,
        gender: "Gender" = None,
        birth_month: int = None,
        birth_year: int = None,
    ):
        from endoreg_db.utils import random_day_by_year, create_mock_patient_name

        created = False

        existing_pathient = cls.objects.filter(patient_hash=patient_hash).first()
        if existing_pathient:
            ic(f"Patient with hash {patient_hash} already exists")
            ic(f"Returning existing patient: {existing_pathient}")
            return existing_pathient, created

        # If no patient with the given hash exists, create a new pseudo patient
        assert center, "Center must be provided to create a new pseudo patient"
        assert gender, "Gender must be provided to create a new pseudo patient"
        assert birth_month, (
            "Birth month must be provided to create a new pseudo patient"
        )
        assert birth_year, "Birth year must be provided to create a new pseudo patient"

        pseudo_dob = random_day_by_year(birth_year)
        gender_name = gender.name
        first_name, last_name = create_mock_patient_name(gender_name)

        ic(f"Creating pseudo patient with hash {patient_hash}")
        ic(f"Generated name: {first_name} {last_name}")

        patient = cls.objects.create(
            first_name=first_name,
            last_name=last_name,
            dob=pseudo_dob,
            patient_hash=patient_hash,
            is_real_person=False,
        )

        patient.save()
        created = True

        return patient, created

    def export_patient_examinations(self):
        """
        Get all associated PatientExaminations, ReportFiles, and Videos for the patient.
        """
        from endoreg_db.models import PatientExamination, ReportFile, Video

        patient_examinations = PatientExamination.objects.filter(patient=self)
        report_files, videos = [], []
        for patient_examination in patient_examinations:
            rr = patient_examination.reportfile_set.all()
            vv = patient_examination.videos.all()

            report_files.extend(rr)
            videos.extend(vv)

        return patient_examinations, report_files, videos

    def get_dob(self) -> datetime.date:
        dob: datetime.date = self.dob
        return dob

    def get_unmatched_report_files(
        self,
    ):  # field: self.report_files; filter: report_file.patient_examination = None
        """Returns all report files for this patient that are not matched to a patient examination."""

        return self.reportfile_set.filter(patient_examination=None)

    def get_unmatched_video_files(
        self,
    ):  # field: self.videos; filter: video.patient_examination = None
        """Returns all video files for this patient that are not matched to a patient examination."""
        return self.videos.filter(patient_examination=None)

    def get_patient_examinations(self):  # field: self.patient_examinations
        """Returns all patient examinations for this patient ordered by date (most recent is first)."""
        return self.patient_examinations.order_by("-date")

    def create_examination(
        self,
        examination_name_str: str = None,
        date_start: datetime = None,
        date_end: datetime = None,
    ):
        """Creates a patient examination for this patient."""

        if examination_name_str:
            from endoreg_db.models import Examination

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

        patient_examination.save()

        return patient_examination

    def create_examination_by_indication(
        self, indication, date_start: datetime = None, date_end: datetime = None
    ):
        from endoreg_db.models import (
            ExaminationIndication,
            Examination,
            PatientExaminationIndication,
        )

        assert isinstance(indication, ExaminationIndication)

        examination = indication.get_examination()

        assert isinstance(examination, Examination)

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
        from endoreg_db.models import Event, PatientEvent

        event = Event.objects.get(name=event_name_str)

        if not date_start:
            date_start = datetime.now()

        patient_event = PatientEvent.objects.create(
            patient=self,
            event=event,
            date_start=date_start,
        )

        return patient_event

    def create_examination_by_report_file(self, report_file: ReportFile):
        """Creates a patient examination for this patient based on the given report file."""
        patient_examination = PatientExamination(patient=self, report_file=report_file)
        patient_examination.save()
        return patient_examination

    @classmethod
    def get_random_gender(cls, p_male=0.5, p_female=0.5):
        """
        Get a Gender object by name (male, female) from the database with given probability.

        :param p_male: Probability of selecting 'male' gender.
        :param p_female: Probability of selecting 'female' gender.
        :return: Gender object selected based on given probabilities.
        """
        from endoreg_db.models import Gender

        # Extract names and probabilities
        gender_names = ["male", "female"]
        probabilities = [p_male, p_female]

        # Debug: print the names and probabilities
        # print(f"Gender names: {gender_names}")
        # print(f"Probabilities: {probabilities}")

        # Select a gender based on the given probabilities
        selected_gender = random.choices(gender_names, probabilities)[0]
        # Debug: print the selected gender
        # print(f"Selected gender: {selected_gender}")

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

        :param center: The center of the patient.
        :return: The created patient.
        """
        from endoreg_db.models import Center

        gender = Patient.get_random_gender()
        last_name, first_name = Patient.get_random_name_for_gender(gender)

        age = Patient.get_random_age()
        dob = Patient.get_dob_from_age(age)

        center = Center.objects.get(name=center)

        patient = Patient.objects.create(
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            gender=gender,
        )
        patient.save()
        return patient

    def age(self):
        """
        Get the age of the patient.

        :return: The age of the patient.
        """
        # calculate correct age based on current date including day and month
        current_date = datetime.now()
        dob = self.dob
        age = (
            current_date.year
            - dob.year
            - ((current_date.month, current_date.day) < (dob.month, dob.day))
        )
        return age

    def create_lab_sample(self, sample_type="generic", date=None, save=True):
        """
        Create a lab sample for this patient.

        :param sample_type: The sample type. Should be either string of the sample types
            name or the sample type object. If not set, the default sample type ("generic") is used.
        :param date: The date of the lab sample.
        :return: The created lab sample.
        """
        from endoreg_db.models import PatientLabSample, PatientLabSampleType

        if date is None:
            date = datetime.now()

        if isinstance(sample_type, str):
            sample_type = PatientLabSampleType.objects.get(name=sample_type)
            assert sample_type is not None, (
                f"Sample type with name '{sample_type}' not found."
            )  #
        elif not isinstance(sample_type, PatientLabSampleType):
            raise ValueError(
                "Sample type must be either a string or a PatientLabSampleType object."
            )

        patient_lab_sample = PatientLabSample.objects.create(
            patient=self, sample_type=sample_type, date=date
        )

        if save:
            patient_lab_sample.save()

        return patient_lab_sample


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = "__all__"
        widgets = {
            "dob": DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
