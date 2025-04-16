# Use faker library to generate fake names by gender
# Use german names by default

from faker import Faker
import gender_guesser.detector as gender_detector


def create_mock_examiner_name() -> tuple[str, str]:
    """
    Generate a mock examiner's name using the Faker library.
    This function creates a tuple with a first name and a last name for a mock examiner. It utilizes the "de_DE" locale for generating German names.
    Returns:
        tuple[str, str]: A tuple containing the first name and the last name.
    """

    fake = Faker("de_DE")
    first_name = fake.first_name()
    last_name = fake.last_name()
    return first_name, last_name


def create_mock_patient_name(gender: str) -> tuple[str, str]:
    """
    Generate a mock patient's name based on the provided gender using the Faker library.
    This function creates a tuple with a first name and a last name for a mock patient. It utilizes the "de_DE" locale for generating German names. When the input gender string is checked:
    - If it contains "male", a male name is generated.
    - If it contains "female", a female name is generated.
    - Otherwise, a generic name is generated without considering gender.
    Parameters:
        gender (str): A string indicating the gender to be used for generating the name.
    Returns:
        tuple[str, str]: A tuple containing the first name and the last name.
    """

    fake = Faker("de_DE")

    if "male" in gender.lower():
        gender = "male"
    elif "female" in gender.lower():
        gender = "female"

    if gender == "male":
        first_name = fake.first_name_male()
        last_name = fake.last_name_male()

    elif gender == "female":
        first_name = fake.first_name_female()
        last_name = fake.last_name_female()

    else:
        first_name = fake.first_name()
        last_name = fake.last_name()

    return first_name, last_name


def guess_name_gender(name: str) -> str:
    """
    Guesses the gender for a given name by using a gender detector and retrieving the corresponding Gender model instance.
    Parameters:
        name (str): The name for which the gender is to be determined.
    Returns:
        Gender: The Gender object corresponding to the detected gender name.
    Raises:
        Gender.DoesNotExist: If no Gender object matching the detected gender is found.
        Exception: For any other exceptions that occur during gender detection or database lookup.
    """

    from endoreg_db.models import Gender

    detector = gender_detector.Detector(case_sensitive=False)
    gender_name = detector.get_gender(name)
    gender = Gender.objects.get(name=gender_name)
    if not gender:
        gender = Gender.objects.get(name="unknown")
    return gender
