from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.db import models

from endoreg_db.models.other.unit import Unit

if TYPE_CHECKING:
    from ...other.emission.emission_factor import EmissionFactor
    from ...other.material import Material
    from ...other.unit import Unit
    from .product import Product

NoProductMaterialValue: TypeAlias = NoneType
ProductMaterialEmission: TypeAlias = tuple[float, "Unit"]


class _ProductMaterialEmissionFactorSource(Protocol):
    unit: "Unit | NoProductMaterialValue"
    value: float


class _ProductMaterialUnitSource(Protocol):
    name: str


class _ProductMaterialSource(Protocol):
    name: str
    emission_factor: "EmissionFactor | NoProductMaterialValue"


class _ProductMaterialProductSource(Protocol):
    name: str


class ProductMaterial(models.Model):
    component: models.CharField[str] = models.CharField(max_length=255)
    material: models.ForeignKey[Material] = models.ForeignKey(
        "Material",
        on_delete=models.CASCADE,
        related_name="material_product_materials",  # Changed related_name
    )
    product: models.ForeignKey[Product] = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="product_product_materials",  # Changed related_name
    )
    unit: models.ForeignKey[Unit] = models.ForeignKey(
        "Unit",
        on_delete=models.CASCADE,
        related_name="unit_product_materials",  # Changed related_name
    )
    quantity: models.FloatField[float] = models.FloatField()

    if TYPE_CHECKING:
        pass

    def get_emission(self) -> ProductMaterialEmission:
        material = cast(_ProductMaterialSource, self.material)
        emission_factor = material.emission_factor
        if emission_factor is None:
            raise Exception(
                "No emission factor for material " + material.name + " found."
            )
        emission_factor_source = cast(
            _ProductMaterialEmissionFactorSource,
            emission_factor,
        )

        unit = cast(_ProductMaterialUnitSource, self.unit)
        emission_unit_source = cast(
            _ProductMaterialUnitSource | NoProductMaterialValue,
            emission_factor_source.unit,
        )
        if emission_unit_source is not None:
            if self.unit != emission_factor_source.unit:
                raise Exception(
                    "Unit mismatch: " + unit.name + " != " + emission_unit_source.name
                )

        emission_value = emission_factor_source.value * self.quantity
        assert isinstance(emission_factor_source.unit, Unit)
        emission_unit = emission_factor_source.unit
        return emission_value, emission_unit

    def __str__(self) -> str:
        product = cast(_ProductMaterialProductSource, self.product)
        material = cast(_ProductMaterialSource, self.material)
        unit = cast(_ProductMaterialUnitSource, self.unit)
        return f"{product.name} - {material.name} - {self.quantity} {unit.name}"
