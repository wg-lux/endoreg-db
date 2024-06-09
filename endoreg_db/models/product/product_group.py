from django.db import models

class ProductGroupManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ProductGroup(models.Model):
    objects = ProductGroupManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    
    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
