from django.db import models

# Class to represent the LocationClassifications of a Finding

class FindingLocationClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingLocationClassification(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    description_de = models.TextField(blank=True, null=True)
    description_en = models.TextField(blank=True, null=True)

    # Many to many relationships:
    findings = models.ManyToManyField('Finding', blank=True, related_name='location_classifications')
    examinations = models.ManyToManyField('Examination', blank=True, related_name='location_classifications')
    finding_types = models.ManyToManyField('FindingType', blank=True, related_name='location_classifications')
    choices = models.ManyToManyField('FindingLocationClassificationChoice', blank=True, related_name='location_classifications')
    
    objects = FindingLocationClassificationManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_choices(self):
        return self.choices.all()

class FindingLocationClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingLocationClassificationChoice(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    description_de = models.TextField(blank=True, null=True)
    description_en = models.TextField(blank=True, null=True)

    # Foreign key relationships: # migrated to organs
    # organs = models.ManyToManyField('Organ', blank=True, related_name='location_choices')
    
    # Optional Descriptors:
    # subcategories_str is a List of strings with names of categorical descriptors
    subcategories = models.JSONField(blank=True, null=True)
    numerical_descriptors = models.JSONField(blank=True, null=True)

    objects = FindingLocationClassificationChoiceManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name