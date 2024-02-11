from django.db import models

class ExaminationTimeTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ExaminationTimeType(models.Model):
    objects = ExaminationTimeTypeManager()
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    examinations = models.ManyToManyField('Examination', blank=True)

    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)

    class Meta:
        verbose_name = 'Examination Time Type'
        verbose_name_plural = 'Examination Time Types'
        ordering = ['name']

