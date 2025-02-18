from endoreg_db.models import PatientExamination
from typing import Optional

def get_patient_examinations() -> PatientExamination:
    """
    Returns all PatientExamination objects from the database.
    """
    return PatientExamination.objects.all()

def get_patient_examinations_without_report_file() -> PatientExamination:
    """
    Returns all PatientExamination objects from the database without a report_file.
    """
    return PatientExamination.objects.filter(report_file__isnull=True)

def get_patient_examinations_without_video() -> PatientExamination:
    """
    Returns all PatientExamination objects from the database without a video.
    """
    return PatientExamination.objects.filter(video__isnull=True)
