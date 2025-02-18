from django.db import models

class ModelMetaManager(models.Manager):
    """
    Custom manager for ModelMeta with additional query methods.
    """
    def get_by_natural_key(self, name: str, version: str) -> "ModelMeta":
        return self.get(name=name, version=version)

class ModelMeta(models.Model):
    """
    Attributes:
        name (str): The name of the model.
        version (str): The version of the model.
        type (ModelType): The type of the model.
        labelset (LabelSet): Associated labels.
        weights (FileField): Path to the model weights.
    """
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=255)
    type = models.ForeignKey("ModelType", on_delete=models.CASCADE, related_name="models")
    labelset = models.ForeignKey("LabelSet", on_delete=models.CASCADE, related_name="models")
    weights = models.FileField(upload_to='weights/')
    description = models.TextField(blank=True, null=True)
    date_created = models.DateTimeField(auto_now_add=True)

    objects = ModelMetaManager()

    def natural_key(self):
        return (self.name, self.version)

    def __str__(self):
        return f"{self.name} (v: {self.version})"