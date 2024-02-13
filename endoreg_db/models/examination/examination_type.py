from django.db import models

class ExaminationTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ExaminationType(models.Model):
    objects = ExaminationTypeManager()
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.name_en
    
    def natural_key(self):
        return (self.name,)
    
