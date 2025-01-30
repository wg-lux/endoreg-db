from django.db import models

class LogTypeManager(models.Manager):
    # Custom manager for LogType; defines name as natural key
    def get_by_natural_key(self, name):
        return self.get(name=name)
    

class LogType(models.Model):
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects = LogTypeManager()
    
    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']
        
    def natural_key(self):
        return (self.name,)
