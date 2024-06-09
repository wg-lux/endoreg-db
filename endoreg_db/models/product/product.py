from django.db import models

class ProductManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
def sum_weights(product_materials):
    # sum up the weights
    weight = 0
    reference_unit = None
    for product_material in product_materials:
        if not reference_unit:
            reference_unit = product_material.unit
        else:
            assert reference_unit == product_material.unit, "ProductMaterial units do not match"
        weight += product_material.quantity

    return weight, reference_unit

def sum_emissions(product_materials):
    # sum up the emissions
    emission = 0
    reference_unit = None
    for product_material in product_materials:
        if not reference_unit:
            reference_unit = product_material.unit
        else:
            assert reference_unit == product_material.unit, "ProductMaterial units do not match"
        emission, emission_unit = product_material.get_emission()
        assert reference_unit == emission_unit, "ProductMaterial units do not match"
        emission += emission

    return emission, reference_unit

class Product(models.Model):
    objects = ProductManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, null=True)
    name_en = models.CharField(max_length=255, null=True)

    transport_route = models.ForeignKey("TransportRoute", on_delete=models.SET_NULL, null=True)
    product_group = models.ForeignKey("ProductGroup", on_delete=models.SET_NULL, null=True)

    def natural_key(self):
        return (self.name,)
    
    def __str__(self):
        return self.name
    
    def get_product_weight(self):
        # check if there is a product material weight
        from .product_material import ProductMaterial
        product_materials = ProductMaterial.objects.filter(product=self, component="product")
        if product_materials:
            return self.get_product_material_weight()
        
        # check if there is a product weight
        #TODO

    def get_package_weight(self):
        # check if there is a package material weight
        from .product_material import ProductMaterial
        product_materials = ProductMaterial.objects.filter(product=self, component="package")
        if product_materials:
            return self.get_package_material_weight()
        
        # check if there is a package weight
        #TODO

    def get_product_material_weight(self):
        # get all materials with component == "product"
        from .product_material import ProductMaterial
        product_materials = ProductMaterial.objects.filter(product=self, component="product")

        return sum_weights(product_materials)
    
    def get_package_material_weight(self):
        # get all materials with component == "package"
        from .product_material import ProductMaterial
        product_materials = ProductMaterial.objects.filter(product=self, component="package")

        return sum_weights(product_materials)

    def get_product_material_emission(self):
        # get all materials with component == "product"
        from .product_material import ProductMaterial
        product_materials = ProductMaterial.objects.filter(product=self, component="product")

        return sum_emissions(product_materials)

    def get_package_material_emission(self):
        # get all materials with component == "package"
        from .product_material import ProductMaterial
        product_materials = ProductMaterial.objects.filter(product=self, component="package")

        return sum_emissions(product_materials)
