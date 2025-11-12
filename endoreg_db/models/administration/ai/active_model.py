from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.metadata import ModelMeta


class ActiveModelManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ActiveModel(models.Model):
    """
    ActiveModel represents an active instance of a model within the application.
    Attributes:
        name (str): A unique identifier for the active model.
        model_meta (ModelMeta, optional): A reference to the metadata of the model. This field acts as a ForeignKey
                                            to the ModelMeta model and can be null.
    Notes:
        - The model_meta attribute is configured with on_delete=models.SET_NULL, meaning that if the related ModelMeta
          record is deleted, model_meta will be set to null.
        - Type hints for model_meta are provided under a TYPE_CHECKING conditional import to avoid circular dependencies,
          importing ModelMeta only when type checking is performed.
    Managers:
        objects (ActiveModelManager): Custom manager providing specialized query capabilities for ActiveModel instances.
    """

    name = models.CharField(max_length=255, unique=True)
    
    model_meta:models.ForeignKey["ModelMeta|None"] = models.ForeignKey(
        'ModelMeta', on_delete=models.SET_NULL, 
        blank=True, null=True
    )

    objects = ActiveModelManager()
