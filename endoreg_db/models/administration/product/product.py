from __future__ import annotations

from collections.abc import Callable, Iterable
from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

from endoreg_db.utils.product.sum_emissions import sum_emissions
from endoreg_db.utils.product.sum_weights import sum_weights

if TYPE_CHECKING:
    from ...other.transport_route import TransportRoute
    from ...other.unit import Unit
    from .product_group import ProductGroup
    from .product_material import ProductMaterial
    from .reference_product import ReferenceProduct
    # from .product_weight import ProductWeight

NoProductValue: TypeAlias = NoneType
ProductMetric: TypeAlias = tuple[float, "Unit | NoProductValue"]
ProductMetricResult: TypeAlias = ProductMetric | NoProductValue
ProductMaterialMetricCalculator: TypeAlias = Callable[
    [Iterable["ProductMaterial"]],
    ProductMetric,
]


class ProductManager(models.Manager["Product"]):
    def get_by_natural_key(self, name: str) -> "Product":
        return self.get(name=name)


class Product(models.Model):
    objects = ProductManager()

    name: models.CharField[str] = models.CharField(max_length=255)

    transport_route: models.ForeignKey[TransportRoute | NoProductValue | None] = (
        models.ForeignKey("TransportRoute", on_delete=models.SET_NULL, null=True)
    )
    product_group: models.ForeignKey[ProductGroup | NoProductValue | None] = (
        models.ForeignKey(
            "ProductGroup",
            on_delete=models.SET_NULL,
            null=True,
            related_name="products",
        )
    )

    if TYPE_CHECKING:
        reference_products: models.QuerySet["ReferenceProduct"]
        product_product_materials: models.QuerySet["ProductMaterial"]

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        result = f"{self.name}"
        if self.product_group:
            result += f" ({self.product_group}, "
        else:
            result += " (no product group, "

        if self.transport_route:
            result += f"{self.transport_route})"
        else:
            result += "no transport route)"

        return result

    def _calculate_material_metric(
        self,
        component: str,
        calculation_func: ProductMaterialMetricCalculator,
    ) -> ProductMetric:
        """Calculate weight or emission for materials of a specific component."""
        from .product_material import (
            ProductMaterial,
        )

        materials = ProductMaterial.objects.filter(product=self, component=component)
        return calculation_func(materials)

    def get_product_weight(self) -> ProductMetricResult:
        """Get the product weight, prioritizing material definitions."""
        from .product_material import ProductMaterial

        if ProductMaterial.objects.filter(product=self, component="product").exists():
            return self.get_product_material_weight()

        return None

    def get_package_weight(self) -> ProductMetricResult:
        """Get the package weight, prioritizing material definitions."""
        from .product_material import ProductMaterial

        if ProductMaterial.objects.filter(product=self, component="package").exists():
            return self.get_package_material_weight()

        return None

    def get_product_material_weight(self) -> ProductMetric:
        """Calculate the total weight based on defined product materials."""
        return self._calculate_material_metric("product", sum_weights)

    def get_package_material_weight(self) -> ProductMetric:
        """Calculate the total weight based on defined package materials."""
        return self._calculate_material_metric("package", sum_weights)

    def get_product_material_emission(self) -> ProductMetric:
        """Calculate the total emissions based on defined product materials."""
        return self._calculate_material_metric("product", sum_emissions)

    def get_package_material_emission(self) -> ProductMetric:
        """Calculate the total emissions based on defined package materials."""
        return self._calculate_material_metric("package", sum_emissions)
