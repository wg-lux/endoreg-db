from typing import TYPE_CHECKING

from django.db import models

from endoreg_db.utils.product.sum_emissions import sum_emissions
from endoreg_db.utils.product.sum_weights import sum_weights

if TYPE_CHECKING:
    from ...other.transport_route import TransportRoute
    from .product_group import ProductGroup
    from .product_material import ProductMaterial
    from .reference_product import ReferenceProduct
    # from .product_weight import ProductWeight


class ProductManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Product(models.Model):
    objects = ProductManager()

    name = models.CharField(max_length=255)

    transport_route = models.ForeignKey("TransportRoute", on_delete=models.SET_NULL, null=True)
    product_group = models.ForeignKey(
        "ProductGroup",
        on_delete=models.SET_NULL,
        null=True,
        related_name="products",
    )

    if TYPE_CHECKING:
        transport_route: models.ForeignKey["TransportRoute|None"]
        product_group: models.ForeignKey["ProductGroup|None"]
        reference_products: models.QuerySet["ReferenceProduct"]
        product_product_materials: models.QuerySet["ProductMaterial"]

    def natural_key(self):
        return (self.name,)

    def __str__(self):
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

    def _calculate_material_metric(self, component: str, calculation_func):
        """Helper method to calculate weight or emission for materials of a specific component."""
        from .product_material import ProductMaterial  # Import locally to avoid circular dependency issues at module level

        materials = ProductMaterial.objects.filter(product=self, component=component)
        return calculation_func(materials)

    def get_product_weight(self):
        """Get the product weight, prioritizing material definitions."""
        from .product_material import ProductMaterial

        # Check if there are specific material definitions for the product component
        if ProductMaterial.objects.filter(product=self, component="product").exists():
            return self.get_product_material_weight()

        # Fallback: check if there is a direct product weight defined (Not implemented yet)
        # TODO: Implement logic for ProductWeight lookup
        return None  # Or appropriate default/error

    def get_package_weight(self):
        """Get the package weight, prioritizing material definitions."""
        from .product_material import ProductMaterial

        # Check if there are specific material definitions for the package component
        if ProductMaterial.objects.filter(product=self, component="package").exists():
            return self.get_package_material_weight()

        # Fallback: check if there is a direct package weight defined (Not implemented yet)
        # TODO: Implement logic for PackageWeight lookup (if different from ProductWeight)
        return None  # Or appropriate default/error

    def get_product_material_weight(self):
        """Calculate the total weight based on defined product materials."""
        return self._calculate_material_metric("product", sum_weights)

    def get_package_material_weight(self):
        """Calculate the total weight based on defined package materials."""
        return self._calculate_material_metric("package", sum_weights)

    def get_product_material_emission(self):
        """Calculate the total emissions based on defined product materials."""
        return self._calculate_material_metric("product", sum_emissions)

    def get_package_material_emission(self):
        """Calculate the total emissions based on defined package materials."""
        return self._calculate_material_metric("package", sum_emissions)
