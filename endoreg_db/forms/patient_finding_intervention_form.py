from django import forms
from endoreg_db.models import (
    Patient, PatientExamination, Finding, 
    FindingClassification, FindingClassificationChoice,
    FindingIntervention, PatientFindingIntervention
)

class PatientFindingInterventionForm(forms.ModelForm):
    patient = forms.ModelChoiceField(queryset=Patient.objects.all(), required=True)
    examination = forms.ModelChoiceField(queryset=PatientExamination.objects.none(), required=True)
    finding = forms.ModelChoiceField(queryset=Finding.objects.none(), required=True)
    classification = forms.ModelChoiceField(queryset=FindingClassification.objects.none(), required=True)
    classification_choice = forms.ModelChoiceField(queryset=FindingClassificationChoice.objects.none(), required=True)
    interventions = forms.ModelMultipleChoiceField(queryset=FindingIntervention.objects.none(), widget=forms.CheckboxSelectMultiple, required=True)

    class Meta:
        model = PatientFindingIntervention
        fields = ["patient", "examination", "finding", "classification", "classification_choice", "interventions"]
