# class to represent unique last_names
# name attribute is natural key
from django.db import models
from hashing import hash_name, NameManager   # Import the hash_name function from the hashing module

'''class LastNameManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)'''

class LastName(models.Model):
    objects = NameManager()
    name = models.CharField(max_length=255, unique=True)
    name_hash = models.CharField(max_length=64, unique=True, editable=False)

    
    def natural_key(self):
        return (self.name,)
    
    def save(self, *args, **kwargs):
        self.name_hash = hash_name(self.name)  # Hash the name before saving
        super(LastName, self).save(*args, **kwargs)

    def __str__(self):
        return self.name
    
# Path: endoreg_db/models/persons/first_name.py
        