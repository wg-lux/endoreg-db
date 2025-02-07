from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from endoreg_db.models.persons.patient.patient import Patient
from endoreg_db.models.patient import PatientExamination
from endoreg_db.forms.patient_form import PatientForm

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    form = PatientForm  
    list_display = ('id', 'first_name', 'last_name', 'dob', 'center')
    search_fields = ('first_name', 'last_name', 'email', 'phone')
    list_filter = ('dob', 'center')
    ordering = ('last_name',)
    actions = ['start_examination']

    def start_examination(self, request, queryset):
        """Redirect to the examination start page"""
        return redirect('/admin/patient_examinations/start/')

@admin.register(PatientExamination)  # ✅ Only one registration now
class PatientExaminationAdmin(admin.ModelAdmin):
    list_display = ('id', 'patient', 'date_start', 'date_end')
    actions = ["start_examination"]

    def start_examination(self, request, queryset):
        return redirect('/admin/patient_examinations/start/')
