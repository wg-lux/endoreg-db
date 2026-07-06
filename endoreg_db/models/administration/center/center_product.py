from __future__ import annotations

from datetime import date
from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.db import models

if TYPE_CHECKING:
    from ...other.unit import Unit
    from ..product import Product
    from ..product.product_group import ProductGroup
    from ..product.reference_product import ReferenceProduct
    from .center import Center

NoCenterProductValue: TypeAlias = NoneType
CenterProductWeight: TypeAlias = tuple[float, "Unit | NoCenterProductValue"]
CenterProductReferenceProduct: TypeAlias = "ReferenceProduct | NoCenterProductValue"


class _CenterProductGroupSource(Protocol):
    reference_product: CenterProductReferenceProduct


class _CenterProductSource(Protocol):
    name: str
    product_group: "ProductGroup | NoCenterProductValue"

    def get_product_weight(self) -> CenterProductWeight | NoCenterProductValue: ...

    def get_package_weight(self) -> CenterProductWeight | NoCenterProductValue: ...


class CenterProduct(models.Model):
    """
    Associates a Product with a Center, indicating its usage on a specific date.

    Attributes:
        product (Product): The product used.
        date_used (date): The date the product was used.
        center (Center): The center where the product was used.
    """

    product: models.ForeignKey[Product] = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="center_products",  # Changed related_name for clarity
    )
    date_used: models.DateField[date] = models.DateField()
    center: models.ForeignKey[Center] = models.ForeignKey(
        "Center",
        on_delete=models.CASCADE,
        related_name="center_products",
    )

    if TYPE_CHECKING:
        product: models.ForeignKey["Product"]
        center: models.ForeignKey["Center"]

    class Meta:
        ordering = ["center", "-date_used", "product"]
        verbose_name = "Center Product Usage"
        verbose_name_plural = "Center Product Usages"

    def __str__(self) -> str:
        return f"{self.product} - {self.center} - {self.date_used}"

    def get_product_name(self) -> str:
        """Returns the name of the product."""
        product = cast(_CenterProductSource, self.product)
        return product.name

    def get_product_group(self) -> "ProductGroup | NoCenterProductValue":
        """Returns the ProductGroup associated with this product."""
        product = cast(_CenterProductSource, self.product)
        return product.product_group

    def get_reference_product(self) -> CenterProductReferenceProduct:
        """Returns the reference Product for this product's group."""
        product_group = self.get_product_group()
        if product_group:
            product_group_source = cast(_CenterProductGroupSource, product_group)
            return product_group_source.reference_product
        return None

    def get_product_weight(self) -> CenterProductWeight | NoCenterProductValue:
        product = cast(_CenterProductSource, self.product)

        return product.get_product_weight()

    def get_package_weight(self) -> CenterProductWeight | NoCenterProductValue:
        product = cast(_CenterProductSource, self.product)

        return product.get_package_weight()
