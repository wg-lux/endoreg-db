from django.db import models

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
