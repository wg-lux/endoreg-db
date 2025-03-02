# Use faker library to generate fake names by gender
# Use german names by default

from faker import Faker
import gender_guesser.detector as gender_detector
from icecream import ic


def create_mock_patient_name(gender: str) -> tuple[str, str]:
    fake = Faker("de_DE")
    if gender.lower() == "male":
        first_name = fake.first_name_male()
        last_name = fake.last_name_male()
    elif gender.lower() == "female":
        first_name = fake.first_name_female()
        last_name = fake.last_name_female()
    else:
        first_name = fake.first_name()
        last_name = fake.last_name()
    return first_name, last_name


def guess_name_gender(name: str) -> str:
    """
    Guess the gender of a given name using the 'gender-guesser' library.

    :param name: Name (typically first name) as a string.
    :return: A string indicating the guessed gender (e.g., 'male', 'female',
             'unknown', 'andy' for androgynous, etc.).
    """
    from endoreg_db.models import Gender

    detector = gender_detector.Detector(case_sensitive=False)
    gender_name = detector.get_gender(name)
    gender = Gender.objects.get(name=gender_name)
    return gender
