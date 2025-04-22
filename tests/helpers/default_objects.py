import random
from endoreg_db.models import (
    Center, 
    Gender, 
    Patient,
    Examination,
    ExaminationIndication,
)
from datetime import date

from endoreg_db.utils import (
    create_mock_examiner_name,
    create_mock_patient_name,
)

DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
DEFAULT_ENDOSCOPE_NAME = "test_endoscope"

DEFAULT_GENDERS = ["male","female","unknown"]
DEFAULT_EXAMINATIONS = ["colonoscopy"]
DEFAULT_INDICATIONS = [
    "colonoscopy",
    "colonoscopy_screening",
    "colonoscopy_lesion_removal_small",
    "colonoscopy_lesion_removal_emr",
    "colonoscopy_lesion_removal_large",
    "colonoscopy_diagnostic_acute_symptomatic",
]


def default_center():
    """
    Create a default Center instance for testing.
    """
    center = Center.objects.get(
        name=DEFAULT_CENTER_NAME,
    )
    return center

def generate_patient(**kwargs):
    """
    Generate a patient with random attributes.
    This function creates a Patient instance with random attributes such as first name, last name, date of birth, and center.
    The attributes are generated using the Faker library and can be overridden by providing keyword arguments.

    Parameters:
        **kwargs: Optional keyword arguments to override default values.

    
    """
    # Set default values
    gender = kwargs.get("gender", random.choice(DEFAULT_GENDERS))

    if not isinstance(gender, Gender):
        assert isinstance(gender, str)
        gender = Gender.objects.get(name=gender)
    first_name, last_name = create_mock_patient_name(gender = gender.name)
    first_name = kwargs.get("first_name", first_name)
    last_name = kwargs.get("last_name", last_name)
    birth_date = kwargs.get("birth_date", "1970-01-01")
    dob = date.fromisoformat(birth_date)
    center = kwargs.get("center", None)
    if center is None:
        center = default_center()
    else:
        center = Center.objects.get(name=center)

    patient = Patient(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center = center,
        gender = gender,
    )

    return patient
    
def get_random_default_examination():
    """
    Get a random examination type from the list of default examinations.
    Returns:
        str: A random examination type.
    """
    examination_name = random.choice(DEFAULT_EXAMINATIONS)

    examination = Examination.objects.get(name=examination_name)
    return examination

def get_random_default_examination_indication():
    """
    Get a random examination indication from the list of default indications.
    Returns:
        str: A random examination indication.
    """
    examination_indication = random.choice(DEFAULT_INDICATIONS)
    all_examination_indications = ExaminationIndication.objects.all()
    try:
        examination_indication = ExaminationIndication.objects.get(name=examination_indication)
        
    except Exception as e:
        print(f"examination_indication: {examination_indication}")
        print(f"all_examination_indications: {all_examination_indications}")
        raise e
    return examination_indication