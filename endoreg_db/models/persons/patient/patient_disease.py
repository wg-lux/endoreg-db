from django.db import models



class PatientDisease(models.Model):
    patient = models.ForeignKey("Patient", on_delete=models.CASCADE)
    disease = models.ForeignKey("Disease", on_delete=models.CASCADE)
    classification_choices = models.ManyToManyField("DiseaseClassificationChoice")
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

    last_update = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.patient} - {self.disease}"
    
