from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .product import Product
    from .reference_product import ReferenceProduct

class ProductGroupManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ProductGroup(models.Model):
    objects = ProductGroupManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)
    
    if TYPE_CHECKING:
        reference_products: models.QuerySet["ReferenceProduct"]
        products: models.QuerySet["Product"]

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return str(self.name)
    
    def get_products(self) -> models.QuerySet["Product"]:
        from .product import Product
        products = self.products.all()
        if products:
            return products
        else:
            # If no products are found, return an empty queryset
            # This is to avoid returning None, which is not a queryset
            # and would cause issues in the calling code.
            return Product.objects.none()
        
    def get_reference_product(self) -> "None | ReferenceProduct":
        reference_products = self.reference_products.all()
        if reference_products:
            if len(reference_products) > 1:
                raise ValueError("There are multiple reference products for this product group.")
            elif len(reference_products) == 1:
                return reference_products[0]
        else:
            return None

