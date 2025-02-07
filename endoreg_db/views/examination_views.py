from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required
from endoreg_db.models.persons.patient.patient import Patient
from endoreg_db.models.patient import PatientExamination

@staff_member_required
def start_examination(request):
    success_message = None

    if request.method == "POST":
        patient_id = request.POST.get("patient")
        patient = Patient.objects.get(id=patient_id)
        exam = PatientExamination.objects.create(patient=patient)
        success_message = f"Examination for {patient.first_name} {patient.last_name} started successfully!"

    patients = Patient.objects.all()
    return render(
        request, 
        "admin/patient_examinations/start_examination.html", 
        {"patients": patients, "success_message": success_message}
    )
