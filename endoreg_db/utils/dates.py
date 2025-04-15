from datetime import datetime, date, timedelta
from random import randint
from calendar import monthrange
from django.utils import timezone
import datetime

# TODO replace used random_day_by_year function implementation when
# creating pseudo patients with new function "random date by age_at_date and examination_date"


def random_day_by_age_at_date(age_at_date: int, examination_date: date) -> date:
    """
    Return a random birth day for a patient with the specified age at the specified examination date.
    """
    examination_year = examination_date.year
    latest_birthdate = examination_date.replace(year=examination_year - age_at_date)
    valid_dates = [latest_birthdate - timedelta(days=i) for i in range(365)]

    select = randint(0, len(valid_dates) - 1)
    birth_date = valid_dates[select]

    return birth_date


def random_day_by_year(year: int) -> date:
    """
    Return a random birth day within the specified year.
    """
    month = randint(1, 12)
    day = randint(1, monthrange(year, month)[1])

    return date(year, month, day)


def random_day_by_month_year(month: int, year) -> date:
    """
    Return a random birth day within the specified month and year.
    """

    day = randint(1, monthrange(year, month)[1])
    return date(year, month, day)


def ensure_aware_datetime(dt):
    """
    Ensures a datetime object is timezone-aware.
    If the datetime is naive (has no timezone info), the current timezone is applied.
    
    Args:
        dt: A datetime object that may be naive
        
    Returns:
        A timezone-aware datetime object
    """
    if dt is None:
        return None
        
    if isinstance(dt, datetime.datetime) and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt
