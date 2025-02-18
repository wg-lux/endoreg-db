from django import forms
from endoreg_db.models import (
    Patient, PatientExamination, Finding, 
    FindingLocationClassification, FindingMorphologyClassification, 
    FindingIntervention, PatientFindingIntervention
)

class PatientFindingInterventionForm(forms.ModelForm):
    patient = forms.ModelChoiceField(queryset=Patient.objects.all(), required=True)
    examination = forms.ModelChoiceField(queryset=PatientExamination.objects.none(), required=True)
    finding = forms.ModelChoiceField(queryset=Finding.objects.none(), required=True)
    location = forms.ModelChoiceField(queryset=FindingLocationClassification.objects.none(), required=True)
    location_choice = forms.ModelChoiceField(queryset=FindingLocationClassification.objects.none(), required=True)
    morphology = forms.ModelChoiceField(queryset=FindingMorphologyClassification.objects.none(), required=True)
    interventions = forms.ModelMultipleChoiceField(queryset=FindingIntervention.objects.none(), widget=forms.CheckboxSelectMultiple, required=True)

    class Meta:
        model = PatientFindingIntervention
        fields = ["patient", "examination", "finding", "location", "location_choice", "morphology", "interventions"]
