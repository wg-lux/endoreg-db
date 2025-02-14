from django.db import models

class LxPermissionManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
#TODO implement base population for permission

class LxPermission(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    objects = LxPermissionManager()
    
    def __str__(self):
        return self.name
    
    def natural_key(self):
        return (self.name,)
