# class to represent unique first-names
# name attribute is natural key

from django.db import models

class FirstNameManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FirstName(models.Model):
    objects = FirstNameManager()
    name = models.CharField(max_length=255, unique=True)
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)