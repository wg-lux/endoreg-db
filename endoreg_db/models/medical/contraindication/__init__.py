from django.db import models

class ContraindicationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class Contraindication(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    description_de = models.TextField(blank=True, null=True)
    description_en = models.TextField(blank=True, null=True)

    objects = ContraindicationManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
