from django.db import models
from typing import TYPE_CHECKING, List

class FindingClassificationTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingClassificationType(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    
    objects = FindingClassificationTypeManager()
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
    
    @classmethod
    def get_required_classifications_for_finding(cls, finding):
        """
        Returns all required finding classification types for a given finding.
        """
        required_classification_types = [
            _ for _ in finding.required_morphology_classification_types.all()
        ]
        return required_classification_types


class FindingClassificationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class FindingClassification(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    classification_type = models.ForeignKey(FindingClassificationType, on_delete=models.CASCADE)

    findings = models.ManyToManyField('Finding', blank=True, related_name='finding_classifications')
    examinations = models.ManyToManyField('Examination', blank=True, related_name='finding_classifications')
    finding_types = models.ManyToManyField('FindingType', blank=True, related_name='finding_classifications')

    objects = FindingClassificationManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Finding, Examination, FindingType, PatientFindingClassification
        )
        classification_type: models.ForeignKey[FindingClassificationType]
        findings: models.QuerySet[Finding]
        examinations: models.QuerySet[Examination]
        finding_types: models.QuerySet[FindingType]
        choices: models.QuerySet['FindingClassificationChoice']
        patient_finding_classifications: models.QuerySet['PatientFindingClassification']

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)

    def get_choices(self):
        return self.choices.all()


class FindingClassificationChoiceManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class FindingClassificationChoice(models.Model):
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True)
    name_en = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    classifications = models.ManyToManyField(
        "FindingClassification", 
        related_name='choices'
    )
    
    subcategories = models.JSONField(
        default = dict
    )

    numerical_descriptors = models.JSONField(
        default = dict
    )

    objects = FindingClassificationChoiceManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            PatientFindingClassification
        )
        classifications: models.QuerySet['FindingClassification']
        patient_finding_classifications: models.QuerySet['PatientFindingClassification']

    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        classifications_names = ", ".join([c.name for c in self.classifications.all()])
        _str = f"{self.name} ({classifications_names})"
        return _str

