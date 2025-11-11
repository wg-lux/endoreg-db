from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from ..product import Product
    from .center import Center


class CenterProduct(models.Model):
    """
    Associates a Product with a Center, indicating its usage on a specific date.

    Attributes:
        product (Product): The product used.
        date_used (date): The date the product was used.
        center (Center): The center where the product was used.
    """

    product = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="center_products",  # Changed related_name for clarity
    )
    date_used = models.DateField()
    center = models.ForeignKey(
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
        return self.product.name

    def get_product_group(self):
        """Returns the ProductGroup associated with this product."""
        return self.product.product_group

    def get_reference_product(self):
        """Returns the reference Product for this product's group."""
        product_group = self.get_product_group()
        if product_group:
            return product_group.reference_product
        return None

    def get_product_weight(self):
        product = self.product

        return product.get_product_weight()

    def get_package_weight(self):
        product = self.product

        return product.get_package_weight()
