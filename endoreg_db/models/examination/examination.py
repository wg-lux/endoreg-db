from django.db import models
from typing import List
class ExaminationManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class Examination(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_de = models.CharField(max_length=100, blank=True, null=True)
    name_en = models.CharField(max_length=100, blank=True, null=True)
    examination_types = models.ManyToManyField('ExaminationType', blank=True)
    objects = ExaminationManager()
    date = models.DateField(blank=True, null=True)
    time = models.TimeField(blank=True, null=True)

    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)
    
    def get_available_findings(self):
        from endoreg_db.models import Finding
        findings:List[Finding] = [_ for _ in self.findings.all()]
        return findings
    

    class Meta:
        verbose_name = 'Examination'
        verbose_name_plural = 'Examinations'
        ordering = ['name']

