from django.db import models

class MaterialManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Material(models.Model):
    objects = MaterialManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)

    def natural_key(self):
        return (self.name,)
