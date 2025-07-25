from django.db import models

class FindingInterventionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingIntervention(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    intervention_types = models.ManyToManyField(
        'FindingInterventionType',
        blank=True,
        related_name='interventions'
    )

    required_lab_values = models.ManyToManyField(
        'LabValue',
        blank=True,
        related_name='required_by_finding_interventions'
    )

    contraindications = models.ManyToManyField(
        'Contraindication',
        blank=True,
        related_name='contraindicating_finding_interventions'
    )

    objects = FindingInterventionManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
    

class FindingInterventionTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingInterventionType(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    objects = FindingInterventionTypeManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
