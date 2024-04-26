from django.db import models

class DiseaseManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class Disease(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = DiseaseManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
class DiseaseClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class DiseaseClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease = models.ForeignKey(Disease, on_delete=models.CASCADE)

    objects = DiseaseClassificationManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name
    
class DiseaseClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)

class DiseaseClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    disease_classification = models.ForeignKey(DiseaseClassification, on_delete=models.CASCADE)

    objects = DiseaseClassificationChoiceManager()

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name