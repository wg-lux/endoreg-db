# # Path: endoreg_db/forms/questionnaires/tto_questionnaire.py
# from endoreg_db.models import TtoQuestionnaire
# from django import forms

# class TtoQuestionnaireForm(forms.ModelForm):
#     class Meta:
#         model = TtoQuestionnaire
#         fields = '__all__'
#         widgets = {
#             # Specify Bootstrap classes for other fields as needed
#             'patient_name': forms.TextInput(attrs={'class': 'form-control'}),
#             'birth_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
#             # You can continue to specify widgets for other fields
#         }


# from django.views.generic.edit import CreateView
# from django.urls import reverse_lazy

# class TtoQuestionnaireCreate(CreateView):
#     model = TtoQuestionnaire
#     form_class = TtoQuestionnaireForm
#     success_url = reverse_lazy('questionnaire_success')  # Redirect to a success page after submission
