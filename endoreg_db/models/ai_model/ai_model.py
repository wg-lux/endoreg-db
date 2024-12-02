from django.db import models    

class AiModelManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class MultilabelVideoSegmentationModel(models.Model):
    objects = AiModelManager()
    
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    description = models.TextField(blank=True, null=True)

    labels = models.ManyToManyField("VideoSegmentationLabel", related_name="models")

    model_type = models.CharField(max_length=255, blank=True, null=True)
    model_subtype = models.CharField(max_length=255, blank=True, null=True)

    version = models.IntegerField(default=1)

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name