# forms.py
from django import forms
from endoreg_db.models import ActiveModel

class ActiveModelForm(forms.ModelForm):
    class Meta:
        model = ActiveModel
        fields = ["model_meta"]  # Or list the specific fields you want to include in the form.
