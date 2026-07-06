from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias

from django.db import models

if TYPE_CHECKING:
    from .product import Product
    from .reference_product import ReferenceProduct

NoProductGroupValue: TypeAlias = NoneType
ProductGroupReference: TypeAlias = "ReferenceProduct | NoProductGroupValue"


class ProductGroupManager(models.Manager["ProductGroup"]):
    def get_by_natural_key(self, name: str) -> "ProductGroup":
        return self.get(name=name)


class ProductGroup(models.Model):
    objects = ProductGroupManager()

    name: models.CharField[str] = models.CharField(max_length=255)

    if TYPE_CHECKING:
        reference_product: "ReferenceProduct"
        products: models.QuerySet["Product"]

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)

    def get_products(self) -> models.QuerySet["Product"]:
        from .product import Product

        products = self.products.all()
        if products:
            return products
        else:
            return Product.objects.none()

    def get_reference_product(self) -> ProductGroupReference:
        return self.reference_product
