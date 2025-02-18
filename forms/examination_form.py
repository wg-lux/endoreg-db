from django import forms
from endoreg_db.models.examination.examination import Examination

class ExaminationForm(forms.ModelForm):
    class Meta:
        model = Examination
        fields = '__all__'
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
        }
