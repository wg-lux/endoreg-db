from django.db import models
from typing import List

class ReferenceProductManager(models.Manager):
    def get_by_natural_key(self, product_name:str, product_group_name:str):
        return self.get(product__name=product_name, product_group__name=product_group_name)

class ReferenceProduct(models.Model):
    name = models.CharField(max_length=255)
    product = models.ForeignKey("Product", on_delete=models.CASCADE)
    product_group = models.OneToOneField("ProductGroup", on_delete=models.CASCADE, related_name="reference_product")
    emission_factor_total = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True, blank = True)
    emission_factor_package = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True, related_name="reference_product_package")
    emission_factor_product = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True, related_name="reference_product_product")
    
    objects = ReferenceProductManager()

    def __str__(self):
        return self.product.name + " (" + self.product_group.name + ")"
    
    def set_emission_factors(self):
        from .product import Product
        from .product_material import ProductMaterial
        from ..emission import EmissionFactor

        product:Product = self.product
        materials = product.product_materials.all()
        emission_factor_name = f"{self.product_group.name}_{product.name}_total_emission_factor"
        emission_factor_package_name = f"{self.product_group.name}_{product.name}_package_emission_factor"
        emission_factor_product_name = f"{self.product_group.name}_{product.name}_product_emission_factor"

        product_emissions = 0
        package_emissions = 0

        product_weight, product_weight_unit = product.get_product_material_weight()
        package_weight, package_weight_unit = product.get_package_material_weight()
        product_emission, product_emission_unit = product.get_product_material_emission()
        package_emission, package_emission_unit = product.get_package_material_emission()

        total_weight = product_weight + package_weight
        total_emission = product_emission + package_emission

        reference_unit = product_weight_unit
        assert reference_unit == package_weight_unit, "Package weight units do not match"
        assert reference_unit == product_emission_unit, "Product emission units do not match"
        assert reference_unit == package_emission_unit, "Package emission units do not match"

        product_emission_factor_value = product_emission / product_weight
        package_emission_factor_value = package_emission / package_weight
        total_emission_factor_value = total_emission / total_weight

        emission_factor, created = EmissionFactor.objects.get_or_create(
            name=emission_factor_name,
            defaults={
                "name": emission_factor_name,
                "value": total_emission_factor_value,
                "unit": reference_unit
            }
        )
        self.emission_factor_total = emission_factor

        emission_factor_package, created = EmissionFactor.objects.get_or_create(
            name=emission_factor_package_name,
            defaults={
                "name": emission_factor_package_name,
                "value": package_emission_factor_value,
                "unit": reference_unit
            }
        )
        self.emission_factor_package = emission_factor_package

        emission_factor_product, created = EmissionFactor.objects.get_or_create(
            name=emission_factor_product_name,
            defaults={
                "name": emission_factor_product_name,
                "value": product_emission_factor_value,
                "unit": reference_unit
            }
        )
        self.emission_factor_product = emission_factor_product

        self.save()

    def get_emission_factor(self, component:str):
        # check if emission_factor_total exists:
        if self.emission_factor_total is None:
            self.set_emission_factors()

        if component == "total":
            return self.emission_factor_total
        elif component == "package":
            return self.emission_factor_package
        elif component == "product":
            return self.emission_factor_product
        else:
            raise Exception("Unknown component: " + component)
        


