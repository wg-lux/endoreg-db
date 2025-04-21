# Django model for the report reader flag
# have name and value
# name is natural key

from django.db import models

class ReportReaderFlagManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ReportReaderFlag(models.Model):
    objects = ReportReaderFlagManager()
    name = models.CharField(max_length=255, unique=True)
    value = models.CharField(max_length=255)
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name