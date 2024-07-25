from ..person import Person
from django import forms
from django.forms import DateInput
from rest_framework import serializers
from ...patient_examination import PatientExamination
from ...data_file import ReportFile
from django.db import models
from faker import Faker
import random
from datetime import datetime

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
    center = models.ForeignKey("Center", on_delete=models.CASCADE, blank=True, null=True)
    

    def __str__(self):
        return self.first_name + " " + self.last_name + " (" + str(self.dob) + ")"
    
    def get_unmatched_report_files(self): #field: self.report_files; filter: report_file.patient_examination = None
        '''Returns all report files for this patient that are not matched to a patient examination.'''

        return self.reportfile_set.filter(patient_examination=None)

    def get_unmatched_video_files(self): #field: self.videos; filter: video.patient_examination = None
        '''Returns all video files for this patient that are not matched to a patient examination.'''
        return self.videos.filter(patient_examination=None)

    def get_patient_examinations(self): #field: self.patient_examinations
        '''Returns all patient examinations for this patient ordered by date (most recent is first).'''
        return self.patient_examinations.order_by('-date')
    
    def create_examination_by_report_file(self, report_file:ReportFile):
        '''Creates a patient examination for this patient based on the given report file.'''
        patient_examination = PatientExamination(patient=self, report_file=report_file)
        patient_examination.save()
        return patient_examination
    
    @classmethod
    def get_random_gender(self, p_male=0.5, p_female=0.5):
        """
        Get a Gender object by name (male, female) from the database with given probability.

        :param p_male: Probability of selecting 'male' gender.
        :param p_female: Probability of selecting 'female' gender.
        :return: Gender object selected based on given probabilities.
        """
        from endoreg_db.models import Gender
        
        # Extract names and probabilities
        gender_names = ["male", "female"]
        probabilities = [0.5, 0.5]
        
        # Debug: print the names and probabilities
        print(f"Gender names: {gender_names}")
        print(f"Probabilities: {probabilities}")
        
        # Select a gender based on the given probabilities
        selected_gender = random.choices(gender_names, probabilities)[0]
        # Debug: print the selected gender
        print(f"Selected gender: {selected_gender}")
        
        # Fetch the corresponding Gender object from the database
        gender_obj = Gender.objects.get(name=selected_gender)
        
        return gender_obj


    @classmethod
    def get_random_age(self, 
            min_age = 55,
            max_age = 90,
            mean_age = 65,
            std_age = 10,
            distribution = "normal"
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
    def get_dob_from_age(self, age, current_date=None):
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
    def get_random_name_for_gender(self, gender_obj, locale="de_DE"):
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
    def create_generic(self, center="gplay_case_generator"):
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
            dob=dob
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
        age = current_date.year - dob.year - ((current_date.month, current_date.day) < (dob.month, dob.day))
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
            assert sample_type is not None, f"Sample type with name '{sample_type}' not found."#
        elif not isinstance(sample_type, PatientLabSampleType):
            raise ValueError("Sample type must be either a string or a PatientLabSampleType object.")
        
        patient_lab_sample = PatientLabSample.objects.create(
            patient=self,
            sample_type=sample_type,
            date=date
        )

        if save:
            patient_lab_sample.save()

        return patient_lab_sample

class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = '__all__'
        widgets = {
            'dob': DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
