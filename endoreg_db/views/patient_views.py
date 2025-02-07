from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required
from endoreg_db.models.patient import PatientExamination
from endoreg_db.models.persons.patient.patient import Patient
from endoreg_db.forms.patient_form import PatientForm

@staff_member_required
def start_examination(request):
    if request.method == 'POST':
        form = PatientForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('/admin/endoreg_db/patientexamination/')
    else:
        form = PatientForm()
    
    return render(request, 'admin/patients/start_examination.html', {'form': form})
