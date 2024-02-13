from django import forms
from ..models import Unit

class UnitForm(forms.Form):
    class Meta:
        model = Unit