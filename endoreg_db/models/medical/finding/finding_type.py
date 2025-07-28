from django.db import models
from typing import TYPE_CHECKING

class FindingTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        """
        Retrieve a FindingType instance by its unique name for natural key deserialization.
        
        Parameters:
            name (str): The unique name of the FindingType to retrieve.
        
        Returns:
            FindingType: The instance matching the given name.
        """
        return self.get(name=name)
    
class FindingType(models.Model):
    name = models.CharField(max_length=100, unique=True) 
    description = models.TextField(blank=True, null=True)

    objects = FindingTypeManager()

    if TYPE_CHECKING:
        from endoreg_db.models import (
            Finding, Examination, FindingClassification, FindingMorphologyClassification
        )
        finding_classifications: models.QuerySet['FindingClassification']
        morphology_classifications: models.QuerySet['FindingMorphologyClassification']
        

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
