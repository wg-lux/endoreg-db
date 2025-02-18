from endoreg_db.models import Video, PatientExamination
from typing import Optional

def get_videos() -> Video:
    """
    Returns all Video objects from the database.
    """
    return Video.objects.all()

def get_video_by_id(id) -> Optional[Video]:
    """Retrieve a Video object by its id.

    Args:
        id (int): The id of the video to retrieve.

    Returns:
        Optional[Video]: The Video object with the given id, or None if it does not exist.
    """
    return Video.objects.get(id=id)

def get_video_without_examination() -> Video:
    """
    Returns all Video objects from the database without an examination.
    """
    return Video.objects.filter(patient_examination__isnull=True)

def get_video_without_patient() -> Video:
    """
    Returns all Video objects from the database without a patient.
    """
    return Video.objects.filter(patient__isnull=True)
