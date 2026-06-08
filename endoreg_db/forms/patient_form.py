from django import forms
from django.forms import ModelForm, Select

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.other.gender import Gender


class PatientForm(ModelForm[Patient]):
    gender = forms.ModelChoiceField(
        queryset=Gender.objects.all(),
        empty_label="Select Gender",
        widget=Select(attrs={"class": "form-control"}),
    )
    center = forms.ModelChoiceField(
        queryset=Center.objects.all(),
        empty_label="Select Center",
        widget=Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Patient
        fields = "__all__"
        widgets = {
            "dob": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }
