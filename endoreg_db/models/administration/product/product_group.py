from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from .product import Product
    from .reference_product import ReferenceProduct

ProductGroupReference: TypeAlias = "ReferenceProduct | None"


class ProductGroup(models.Model):
    objects = NameNaturalKeyManager["ProductGroup"]()

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )

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
