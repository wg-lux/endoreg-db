'''Module for distribution models.'''

from .base_value_distribution import BaseValueDistribution
from .numeric_value_distribution import NumericValueDistribution
from .single_categorical_value_distribution import SingleCategoricalValueDistribution
from .multiple_categorical_value_distribution import MultipleCategoricalValueDistribution
from .date_value_distribution import DateValueDistribution


__all__ = [
    'BaseValueDistribution',
    'NumericValueDistribution',
    'SingleCategoricalValueDistribution',
    'MultipleCategoricalValueDistribution',
    'DateValueDistribution',
]

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
