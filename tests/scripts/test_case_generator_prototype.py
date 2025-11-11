import pytest
from datetime import time

from scripts.case_generator import prototype


@pytest.fixture
def casegen_prereqs(db):
    from endoreg_db.models import (
        Center,
        FirstName,
        Gender,
        LastName,
        PatientLabSampleType,
    )

    Center.objects.all().delete()
    Gender.objects.all().delete()

    first_name, _ = FirstName.objects.get_or_create(name="CaseFirst")
    last_name, _ = LastName.objects.get_or_create(name="CaseLast")

    center = Center.objects.create(name="casegen_center")
    center.first_names.add(first_name)
    center.last_names.add(last_name)

    gender = Gender.objects.create(name="female")

    PatientLabSampleType.objects.get_or_create(name="generic")

    return {"center": center, "gender": gender}


def test_ensure_lab_values_creates_measurement(casegen_prereqs):
    from endoreg_db.models import LabValue, PatientLabValue, Unit

    context = prototype.initialize_generation_context()
    patient = context.ensure_patient()

    unit = Unit.objects.create(name="mg/dL")
    lab_value = LabValue.objects.create(
        name="glucose",
        default_unit=unit,
        default_normal_range={"min": 70, "max": 110},
    )

    prototype.ensure_lab_values(context, patient, {"lab_values": [lab_value]})

    patient_lab_value = PatientLabValue.objects.get(patient=patient, lab_value=lab_value)
    assert patient_lab_value in context.lab_values
    assert patient_lab_value.sample is not None
    assert patient_lab_value.unit == unit


def test_ensure_medications_creates_patient_medication(casegen_prereqs):
    from endoreg_db.models import Medication, MedicationIntakeTime, PatientMedication, Unit

    context = prototype.initialize_generation_context()
    patient = context.ensure_patient()

    unit = Unit.objects.create(name="tablet")
    medication = Medication.objects.create(name="omeprazole", default_unit=unit)
    intake_time = MedicationIntakeTime.objects.create(name="morning", time=time(hour=8, minute=0))

    prototype.ensure_medications(
        context,
        patient,
        {"medications": [medication], "medication_intake_times": [intake_time]},
    )

    patient_medication = PatientMedication.objects.get(patient=patient, medication=medication)
    assert patient_medication in context.medications
    assert patient_medication.intake_times.count() == 1
    assert patient_medication.intake_times.first() == intake_time


def test_ensure_events_creates_patient_event(casegen_prereqs):
    from endoreg_db.models import Event, PatientEvent

    context = prototype.initialize_generation_context()
    patient = context.ensure_patient()

    event = Event.objects.create(name="colonoscopy_preparation")

    prototype.ensure_events(context, patient, {"events": [event]})

    patient_event = PatientEvent.objects.get(patient=patient, event=event)
    assert patient_event in context.events


def test_ensure_examinations_and_findings_creates_patient_finding(casegen_prereqs):
    from endoreg_db.models import Examination, Finding, PatientFinding

    context = prototype.initialize_generation_context()

    exam = Examination.objects.create(name="colonoscopy")
    finding = Finding.objects.create(name="polyp")
    exam.findings.add(finding)

    prototype.ensure_examinations_and_findings(
        context,
        {"examinations": [exam], "findings": [finding]},
    )

    assert context.examinations
    patient_exam = context.examinations[0]

    patient_finding = PatientFinding.objects.get(patient_examination=patient_exam, finding=finding)
    assert patient_finding in context.findings
