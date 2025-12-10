from datetime import date, timedelta
from typing import Tuple, Optional
from faker import Faker
import random


def fake_name_with_similar_dob_and_gender(
    gender: str,
    dob: date,
    *,
    year_tolerance: int = 3,
    locale: str = "de_DE",
    seed: Optional[int] = None,
) -> Tuple[str, str, date]:
    """
    Generate a fake name with the same gender and a similar date of birth.

    Args:
        gender: "male" or "female"
        dob: Original date of birth
        year_tolerance: Maximum age difference in years
        locale: Faker locale (default: German)
        seed: Optional reproducible seed

    Returns:
        (full_name, fake_dob)
    """

    if gender not in {"male", "female"}:
        raise ValueError("gender must be 'male' or 'female'")

    fake = Faker(locale)

    if seed is not None:
        Faker.seed(seed)
        random.seed(seed)

    # --- Generate gender-safe name ---
    if gender == "male":
        first_name = fake.first_name_male()
    else:
        first_name = fake.first_name_female()

    last_name = fake.last_name()
    full_name = f"{first_name} {last_name}"

    # --- Generate similar DOB ---
    days_range = year_tolerance * 365
    offset_days = random.randint(-days_range, days_range)
    fake_dob = dob + timedelta(days=offset_days)

    return first_name, last_name, fake_dob
