from django.db import models 

class PatientFindingIntervention(models.Model):
    patient_finding = models.ForeignKey(
        'PatientFinding', 
        on_delete=models.CASCADE, 
        related_name='interventions'
    )
    intervention = models.ForeignKey(
        'FindingIntervention',
        on_delete=models.CASCADE,
        related_name='patient_finding_interventions'
    )
    state = models.CharField(max_length=100, blank=True, null=True)
    time_start = models.DateTimeField(blank=True, null=True)
    time_end = models.DateTimeField(blank=True, null=True)
    date = models.DateField(blank=True, null=True)

    def __str__(self):
        return self.intervention.name

    def natural_key(self):
        return (self.intervention.name,)
    

    objects = models.Manager()
