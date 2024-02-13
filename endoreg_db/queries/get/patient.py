from endoreg_db.models import Patient

def get_patients() -> Patient:
    """
    Returns all Patient objects from the database.
    """
    return Patient.objects.all()

def get_patients_without_dob() -> Patient:
    """
    Returns all Patient objects from the database without a date of birth.
    """
    return Patient.objects.filter(dob__isnull=True)

