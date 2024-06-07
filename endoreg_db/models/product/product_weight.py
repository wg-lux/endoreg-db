from django.db import models
import pandas as pd

class ProductWeightManager(models.Manager):
    def get_by_natural_key(self, product, product_group, weight):
        return self.get(product=product, product_group=product_group, weight=weight)
    
class ProductWeight(models.Model):
    objects = ProductWeightManager()

    name = models.CharField(max_length=255, null = True)
    product = models.ForeignKey("Product", on_delete=models.CASCADE)
    measured = models.FloatField(null=True)
    verified = models.FloatField(null=True)
    manufacturer = models.FloatField(null=True)
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)

    def get_weight(self):
        if not pd.isnull(self.verified):
            return self.verified
        elif not pd.isnull(self.measured):
            return self.measured
        elif not pd.isnull(self.manufacturer):
            return self.manufacturer
        else:
            return None
