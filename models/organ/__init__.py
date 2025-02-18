from django.db import models

class OrganManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class Organ(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    description_de = models.TextField(blank=True, null=True)
    description_en = models.TextField(blank=True, null=True)

    location_choices = models.ManyToManyField(
        'FindingLocationClassificationChoice', 
        blank=True, related_name='organs'
    )

    objects = OrganManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name