from .person import Person
from django import forms
from django.forms import DateInput
from rest_framework import serializers
from ..patient_examination import PatientExamination
from ..data_file import ReportFile
from django.db import models

class Patient(Person):
    """
    A class representing a patient.

    Attributes inhereted from Person:
        first_name (str): The first name of the patient.
        last_name (str): The last name of the patient.
        dob (datetime.date): The date of birth of the patient.
        gender (str): The gender of the patient.
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
