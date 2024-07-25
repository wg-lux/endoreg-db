from django.db import models
import numpy as np
from scipy.stats import skewnorm

class BaseValueDistribution(models.Model):
    """
    Abstract base class for value distributions.
    """
    name = models.CharField(max_length=100)

    class Meta:
        abstract = True

    def generate_value(self):
        """
        Generate a value based on the distribution rules.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement this method")
    
    def natural_key(self):
        return (self.name,)

class NumericValueDistributionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class NumericValueDistribution(BaseValueDistribution):
    """
    Numeric value distribution model.
    Supports uniform, normal, and skewed normal distributions with hard limits.
    """
    objects = NumericValueDistributionManager()
    DISTRIBUTION_CHOICES = [
        ('uniform', 'Uniform'),
        ('normal', 'Normal'),
        ('skewed_normal', 'Skewed Normal'),
    ]

    distribution_type = models.CharField(max_length=20, choices=DISTRIBUTION_CHOICES)
    min_value = models.FloatField()
    max_value = models.FloatField()
    mean = models.FloatField(null=True, blank=True)
    std_dev = models.FloatField(null=True, blank=True)
    skewness = models.FloatField(null=True, blank=True)

    def generate_value(self):
        if self.distribution_type == 'uniform':
            return np.random.uniform(self.min_value, self.max_value)
        elif self.distribution_type == 'normal':
            value = np.random.normal(self.mean, self.std_dev)
            return np.clip(value, self.min_value, self.max_value)
        elif self.distribution_type == 'skewed_normal':
            value = skewnorm.rvs(a=self.skewness, loc=self.mean, scale=self.std_dev)
            return np.clip(value, self.min_value, self.max_value)
        else:
            raise ValueError("Unsupported distribution type")
        
    
class SingleCategoricalValueDistributionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class SingleCategoricalValueDistribution(BaseValueDistribution):
    """
    Single categorical value distribution model.
    Assigns a single value based on specified probabilities.
    """
    objects = SingleCategoricalValueDistributionManager()
    categories = models.JSONField()  # { "category": "probability", ... }

    def generate_value(self):
        categories, probabilities = zip(*self.categories.items())
        return np.random.choice(categories, p=probabilities)
    

class MultipleCategoricalValueDistributionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class MultipleCategoricalValueDistribution(BaseValueDistribution):
    """
    Multiple categorical value distribution model.
    Assigns a specific number or varying number of values based on probabilities.
    """
    objects = MultipleCategoricalValueDistributionManager()
    categories = models.JSONField()  # { "category": "probability", ... }
    min_count = models.IntegerField()
    max_count = models.IntegerField()
    count_distribution_type = models.CharField(max_length=20, choices=[('uniform', 'Uniform'), ('normal', 'Normal')])
    count_mean = models.FloatField(null=True, blank=True)
    count_std_dev = models.FloatField(null=True, blank=True)

    def generate_value(self):
        if self.count_distribution_type == 'uniform':
            count = np.random.randint(self.min_count, self.max_count + 1)
        elif self.count_distribution_type == 'normal':
            count = int(np.random.normal(self.count_mean, self.count_std_dev))
            count = np.clip(count, self.min_count, self.max_count)
        else:
            raise ValueError("Unsupported count distribution type")

        categories, probabilities = zip(*self.categories.items())
        return list(np.random.choice(categories, size=count, p=probabilities))


class DateValueDistributionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

from datetime import date, timedelta
class DateValueDistribution(BaseValueDistribution):
    """
    Assign date values based on specified distribution.
    Expects distribution_type (uniform, normal) and mode (date, timedelta) and based on this either
    date_min, date_max, date_mean, date_std_dev or
    timedelta_days_min, timedelta_days_max, timedelta_days_mean, timedelta_days_std_dev
    """
    objects = DateValueDistributionManager()
    name = models.CharField(max_length=100)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
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
        
# Example Usage
# Numeric distribution for age
# age_distribution = NumericValueDistribution.objects.create(
#     name='Age Distribution',
#     distribution_type='normal',
#     min_value=0,
#     max_value=100,
#     mean=50,
#     std_dev=15
# )

# # Single categorical distribution for gender
# gender_distribution = SingleCategoricalValueDistribution.objects.create(
#     name='Gender Distribution',
#     categories={'male': 0.5, 'female': 0.5}
# )

# # Multiple categorical distribution for symptoms
# symptoms_distribution = MultipleCategoricalValueDistribution.objects.create(
#     name='Symptoms Distribution',
#     categories={'fever': 0.3, 'cough': 0.4, 'fatigue': 0.2, 'nausea': 0.1},
#     min_count=1,
#     max_count=3,
#     count_distribution_type='normal',
#     count_mean=2,
#     count_std_dev=0.5
# )
