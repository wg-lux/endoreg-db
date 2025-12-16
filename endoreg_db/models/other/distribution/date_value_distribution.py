"""Model for date value distribution"""

from datetime import date, timedelta

import numpy as np
from django.db import models

from .base_value_distribution import BaseValueDistribution


class DateValueDistributionManager(models.Manager):
    """Object manager for DateValueDistribution"""

    def get_by_natural_key(self, name):
        """Retrieve a DateValueDistribution by its natural key."""
        return self.get(name=name)


class DateValueDistribution(BaseValueDistribution):
    """
    Assign date values based on specified distribution.
    Expects distribution_type (uniform, normal) and mode (date, timedelta) and based on this either
    date_min, date_max, date_mean, date_std_dev or
    timedelta_days_min, timedelta_days_max, timedelta_days_mean, timedelta_days_std_dev
    """

    objects = DateValueDistributionManager()
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    DISTRIBUTION_CHOICES = [
        ("uniform", "Uniform"),
        ("normal", "Normal"),
    ]
    MODE_CHOICES = [
        ("date", "Date"),
        ("timedelta", "Timedelta"),
    ]

    distribution_type = models.CharField(max_length=20, choices=DISTRIBUTION_CHOICES)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES)

    # Date-related fields
    date_min = models.DateField(blank=True, null=True)
    date_max = models.DateField(blank=True, null=True)
    date_mean = models.DateField(blank=True, null=True)
    date_std_dev = models.IntegerField(blank=True, null=True)  # Standard deviation in days

    # Timedelta-related fields
    timedelta_days_min = models.IntegerField(blank=True, null=True)
    timedelta_days_max = models.IntegerField(blank=True, null=True)
    timedelta_days_mean = models.IntegerField(blank=True, null=True)
    timedelta_days_std_dev = models.IntegerField(blank=True, null=True)

    # create *_safe properties for dates and timedeltas
    @property
    def date_min_safe(self):
        _date = self.date_min
        if _date is None:
            raise ValueError("date_min is not set")
        return _date

    @property
    def date_max_safe(self):
        _date = self.date_max
        if _date is None:
            raise ValueError("date_max is not set")
        return _date

    @property
    def date_mean_safe(self):
        _date = self.date_mean
        if _date is None:
            raise ValueError("date_mean is not set")
        return _date

    @property
    def date_std_dev_safe(self):
        _std_dev = self.date_std_dev
        if _std_dev is None:
            raise ValueError("date_std_dev is not set")
        return _std_dev

    @property
    def timedelta_days_min_safe(self):
        _min = self.timedelta_days_min
        if _min is None:
            raise ValueError("timedelta_days_min is not set")
        return _min

    @property
    def timedelta_days_max_safe(self):
        _max = self.timedelta_days_max
        if _max is None:
            raise ValueError("timedelta_days_max is not set")
        return _max

    @property
    def timedelta_days_mean_safe(self):
        _mean = self.timedelta_days_mean
        if _mean is None:
            raise ValueError("timedelta_days_mean is not set")
        return _mean

    @property
    def timedelta_days_std_dev_safe(self):
        _std_dev = self.timedelta_days_std_dev
        if _std_dev is None:
            raise ValueError("timedelta_days_std_dev is not set")
        return _std_dev

    def generate_value(self):
        if self.mode == "date":
            return self._generate_date_value()
        elif self.mode == "timedelta":
            return self._generate_timedelta_value()
        else:
            raise ValueError("Unsupported mode")

    def _generate_date_value(self):
        # UNTESTED
        if self.distribution_type == "uniform":
            start_date = self.date_min_safe.toordinal()
            end_date = self.date_max_safe.toordinal()
            random_ordinal = np.random.randint(start_date, end_date)
            return date.fromordinal(random_ordinal)
        elif self.distribution_type == "normal":
            mean_ordinal = self.date_mean_safe.toordinal()
            std_dev_days = self.date_std_dev_safe
            random_ordinal = int(np.random.normal(mean_ordinal, std_dev_days))
            random_ordinal = np.clip(random_ordinal, self.date_min_safe.toordinal(), self.date_max_safe.toordinal())
            return date.fromordinal(random_ordinal)
        else:
            raise ValueError("Unsupported distribution type")

    def _generate_timedelta_value(self):
        if self.distribution_type == "uniform":
            random_days = np.random.randint(self.timedelta_days_min_safe, self.timedelta_days_max_safe + 1)

        elif self.distribution_type == "normal":
            random_days = int(np.random.normal(self.timedelta_days_mean_safe, self.timedelta_days_std_dev_safe))
            random_days = np.clip(random_days, self.timedelta_days_min_safe, self.timedelta_days_max_safe)

        else:
            raise ValueError("Unsupported distribution type")

        current_date = date.today()
        generated_date = current_date - timedelta(days=random_days)

        return generated_date
