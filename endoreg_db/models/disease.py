from django.db import models

class Disease(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
class DiseaseClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease = models.ForeignKey(Disease, on_delete=models.CASCADE)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
class DiseaseClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease_classification = models.ForeignKey(DiseaseClassification, on_delete=models.CASCADE)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name