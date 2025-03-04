# import os

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

from endoreg_db.utils.dates import random_day_by_age_at_date, random_day_by_month_year
from django.test import TestCase
from datetime import date
from icecream import ic


class TestCenter(TestCase):
    def setUp(self):
        pass

    def test_random_day_by_age_at_date(self):
        age = 10
        examination_date = date(2020, 1, 1)
        birth_date = random_day_by_age_at_date(age, examination_date)

        timedelta_ex_birth = examination_date - birth_date
        ic(f"The patient is {age} years old.")
        ic(f"The examination date is {examination_date}.")
        ic(f"The pseudo birth date is {birth_date}.")
        ic(f"The patient is {timedelta_ex_birth.days} days old.")

        self.assertEqual(timedelta_ex_birth.days // 365, age)
