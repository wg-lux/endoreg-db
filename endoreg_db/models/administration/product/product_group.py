from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import (
        Product,
        ReferenceProduct,
    )

class ProductGroupManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class ProductGroup(models.Model):
    objects = ProductGroupManager()

    name = models.CharField(max_length=255)
    
    if TYPE_CHECKING:
        reference_product: "ReferenceProduct"
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
            return Product.objects.none()
        
    def get_reference_product(self) -> "None | ReferenceProduct":
        return self.reference_product 

