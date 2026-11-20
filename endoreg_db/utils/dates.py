"""Calendar date selection and timezone normalization helpers."""

from calendar import monthrange
from datetime import date, datetime, timedelta
from random import randint

from django.utils import timezone

# TODO replace used random_day_by_year function implementation when
# creating pseudo patients with new function "random date by age_at_date and examination_date"


def random_day_by_age_at_date(age_at_date: int, examination_date: date) -> date:
    """Select from the 365 days ending at the age-adjusted examination date.

    An invalid year replacement, such as February 29 in a non-leap year,
    raises ValueError.
    """
    latest_birthdate = examination_date.replace(
        year=examination_date.year - age_at_date
    )
    candidate_birthdates = [
        latest_birthdate - timedelta(days=days_before) for days_before in range(365)
    ]
    selected_index = randint(0, len(candidate_birthdates) - 1)
    return candidate_birthdates[selected_index]


def random_day_by_year(year: int) -> date:
    """Choose a month uniformly, then a valid day within it."""
    month = randint(1, 12)
    return random_day_by_month_year(month, year)


def random_day_by_month_year(month: int, year: int) -> date:
    """Return a uniformly selected date within the specified month and year."""
    days_in_month = monthrange(year, month)[1]
    day = randint(1, days_in_month)
    return date(year, month, day)


def ensure_aware_datetime(dt: datetime) -> datetime:
    """Apply the current timezone to naive values; preserve aware values."""
    if timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
