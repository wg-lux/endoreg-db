from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from ...metadata.model_meta import ModelMeta


class ActiveModel(models.Model):
    """
    ActiveModel represents an active instance of a model within the application.
    Attributes:
        name (str): A unique identifier for the active model.
        model_meta (ModelMeta): A reference to the metadata of the model. This
            field acts as a ForeignKey to the ModelMeta model and can be null.
    Notes:
        - The model_meta attribute is configured with on_delete=models.SET_NULL, meaning that if the related ModelMeta
          record is deleted, model_meta will be set to null.
        - Type hints for model_meta are provided under a TYPE_CHECKING conditional import to avoid circular dependencies,
          importing ModelMeta only when type checking is performed.
    Managers:
        objects (NameNaturalKeyManager): Resolves ActiveModel instances by natural key.
    """

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)

    model_meta: models.ForeignKey[ModelMeta | None] = models.ForeignKey(
        "ModelMeta", on_delete=models.SET_NULL, blank=True, null=True
    )

    objects = NameNaturalKeyManager["ActiveModel"]()
