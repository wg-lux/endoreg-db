'''Model for medication indication type.'''
from django.db import models

class MedicationIndicationTypeManager(models.Manager):
    '''Manager for the medication indication type model.'''
    def get_by_natural_key(self, name):
        '''Retrieve a medication indication type by its natural key.'''
        return self.get(name=name)

class MedicationIndicationType(models.Model):
    '''Model representing a medication indication type.'''
    name = models.CharField(max_length=255, unique=True)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    objects = MedicationIndicationTypeManager()

    def natural_key(self):
        '''Return the natural key for the medication indication type.'''
        return (self.name,)

    def __str__(self):
        return str(self.name)
    
    @classmethod
    def get_random_indication_by_type(cls, name) -> "MedicationIndication":
        '''Return a random medication indication of the given type.'''
        return cls.objects.get(name=name).medication_indications.order_by('?').first()
    

    def get_random_medication_indication(self):
        '''Return a random medication indication of this type.'''
        from endoreg_db.models import MedicationIndication
        return MedicationIndication.objects.filter(indication_type=self).order_by('?').first()
