from __future__ import annotations
from typing import TYPE_CHECKING, ClassVar, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from ..administration.product.product_material import ProductMaterial


class Material(models.Model):
    objects: ClassVar[NameNaturalKeyManager["Material"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        NameNaturalKeyManager["Material"]()
    )

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    emission_factor: models.ForeignKey[Any] = models.ForeignKey(
        "EmissionFactor", on_delete=models.SET_NULL, null=True
    )

    if TYPE_CHECKING:

        @property
        def material_product_materials(self) -> models.QuerySet["ProductMaterial"]: ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        result = f"{self.name} - EmissionFactor: {self.emission_factor}"
        return result
