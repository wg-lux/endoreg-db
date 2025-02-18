from django import forms
from django.forms import ModelForm, Select
from endoreg_db.models.persons.patient.patient import Patient
from django import forms
from django.forms import ModelForm, Select
from endoreg_db.models.persons.patient.patient import Patient
from endoreg_db.models.persons.gender import Gender  
from endoreg_db.models.center import Center  
class PatientForm(ModelForm):
    gender = forms.ModelChoiceField(
        queryset=Gender.objects.all(),  
        empty_label="Select Gender",  
        widget=Select(attrs={'class': 'form-control'})
    )
    center = forms.ModelChoiceField(
        queryset=Center.objects.all(),  
        empty_label="Select Center",  
        widget=Select(attrs={'class': 'form-control'})
    )

    class Meta:
        model = Patient
        fields = '__all__'
        widgets = {
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }
