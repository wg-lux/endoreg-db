from django.db import models

class EmissionFactorManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class EmissionFactor(models.Model):
    objects = EmissionFactorManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    value = models.FloatField()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name