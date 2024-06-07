from itertools import product
from django.db import models

class CenterProduct(models.Model):
    product = models.ForeignKey("Product", on_delete=models.CASCADE)
    date_used = models.DateField()
    center = models.ForeignKey("Center", on_delete=models.CASCADE)


    def get_product_group(self):
        product = self.product
        product_group = product.product_group

        return product_group
    
    def get_reference_product(self):
        product_group = self.get_product_group()
        reference_product = product_group.reference_product

        return reference_product
    
    def get_product_weight(self):
        product = self.product
        weight, unit = product.get_product_weight()

        return weight, unit
    
    def get_package_weight(self):
        product = self.product
        weight, unit = product.get_package_weight()

        return weight, unit
    
    