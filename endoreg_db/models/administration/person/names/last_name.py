
from django.db import models

class LastNameManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class LastName(models.Model):
    objects = LastNameManager()
    name = models.CharField(max_length=255, unique=True)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)
    
# Path: endoreg_db/models/persons/first_name.py
        