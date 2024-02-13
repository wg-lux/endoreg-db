from django.db import models

class EndoscopeManager(models.Manager):
    def get_by_natural_key(self, name, sn):
        return self.get(name=name, sn=sn)
    
class Endoscope(models.Model):
    objects = EndoscopeManager()
    
    name = models.CharField(max_length=255)
    sn = models.CharField(max_length=255)
    
    def natural_key(self):
        return (self.name, self.sn)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Endoscope'
        verbose_name_plural = 'Endoscopes'

class EndoscopeTypeManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class EndoscopeType(models.Model):
    objects = EndoscopeTypeManager()
    
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        verbose_name = 'Endoscope Type'
        verbose_name_plural = 'Endoscope Types'
