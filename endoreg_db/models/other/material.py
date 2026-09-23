from __future__ import annotations
from typing import TYPE_CHECKING, ClassVar, Any

from django.db import models

if TYPE_CHECKING:
    from ..administration.product.product_material import ProductMaterial


class MaterialManager(models.Manager["Material"]):
    def get_by_natural_key(self, name: str) -> "Material":
        return self.get(name=name)


class Material(models.Model):
    objects: ClassVar[MaterialManager] = MaterialManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
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
