from django.db import models

class ActiveModelManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ActiveModel(models.Model):
    name = models.CharField(max_length=255, unique=True)
    model_meta = models.OneToOneField('ModelMeta', on_delete=models.SET_NULL, blank=True, null=True)
