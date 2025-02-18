from django.db import models

class GenderManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class Gender(models.Model):
    """A class representing gender."""
    objects = GenderManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    abbreviation = models.CharField(max_length=255, null=True)
    description = models.TextField(blank=True, null=True)

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name

