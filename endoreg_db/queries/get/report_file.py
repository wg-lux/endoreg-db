from endoreg_db.models import ReportFile
from typing import Optional

def get_report_files() -> ReportFile:
    """
    Returns all ReportFile objects from the database.
    """
    return ReportFile.objects.all()

def get_report_file_by_id(id) -> Optional[ReportFile]:
    """Retrieve a ReportFile object by its id.

    Args:
        id (int): The id of the report_file to retrieve.

    Returns:
        Optional[ReportFile]: The ReportFile object with the given id, or None if it does not exist.
    """
    return ReportFile.objects.get(id=id)

def get_report_files_without_examination() -> ReportFile:
    """
    Returns all ReportFile objects from the database without an examination.
    """
    return ReportFile.objects.filter(patient_examination__isnull=True)

def get_report_files_without_patient() -> ReportFile:
    """
    Returns all ReportFile objects from the database without a patient.
    """
    return ReportFile.objects.filter(patient__isnull=True)


