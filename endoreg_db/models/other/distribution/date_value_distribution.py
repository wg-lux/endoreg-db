'''Model for date value distribution'''

from datetime import date, timedelta
from django.db import models
import numpy as np

from .base_value_distribution import BaseValueDistribution

class DateValueDistributionManager(models.Manager):
    '''Object manager for DateValueDistribution'''
    def get_by_natural_key(self, name):
        '''Retrieve a DateValueDistribution by its natural key.'''
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
        ('uniform', 'Uniform'),
        ('normal', 'Normal'),
    ]
    MODE_CHOICES = [
        ('date', 'Date'),
        ('timedelta', 'Timedelta'),
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

    def generate_value(self):
        if self.mode == 'date':
            return self._generate_date_value()
        elif self.mode == 'timedelta':
            return self._generate_timedelta_value()
        else:
            raise ValueError("Unsupported mode")

    def _generate_date_value(self):
        #UNTESTED
        if self.distribution_type == 'uniform':
            start_date = self.date_min.toordinal()
            end_date = self.date_max.toordinal()
            random_ordinal = np.random.randint(start_date, end_date)
            return date.fromordinal(random_ordinal)
        elif self.distribution_type == 'normal':
            mean_ordinal = self.date_mean.toordinal()
            std_dev_days = self.date_std_dev
            random_ordinal = int(np.random.normal(mean_ordinal, std_dev_days))
            random_ordinal = np.clip(random_ordinal, self.date_min.toordinal(), self.date_max.toordinal())
            return date.fromordinal(random_ordinal)
        else:
            raise ValueError("Unsupported distribution type")

    def _generate_timedelta_value(self):
        if self.distribution_type == 'uniform':
            random_days = np.random.randint(self.timedelta_days_min, self.timedelta_days_max + 1)
            
            
        elif self.distribution_type == 'normal':
            random_days = int(np.random.normal(self.timedelta_days_mean, self.timedelta_days_std_dev))
            random_days = np.clip(random_days, self.timedelta_days_min, self.timedelta_days_max)
            
        else:
            raise ValueError("Unsupported distribution type")
        
        current_date = date.today()
        generated_date = current_date - timedelta(days=random_days)
        print(generated_date)
        return(generated_date)