from django.db import models

class ProductMaterial(models.Model):
    component = models.CharField(max_length=255)
    material = models.ForeignKey("Material", on_delete=models.CASCADE)
    product = models.ForeignKey("Product", on_delete=models.CASCADE, related_name="product_materials")
    unit = models.ForeignKey("Unit", on_delete=models.CASCADE)
    quantity = models.FloatField()

    def get_emission(self):
        from ..emission import EmissionFactor
        emission_factor:EmissionFactor = self.material.emission_factor
        if emission_factor is None:
            raise Exception("No emission factor for material " + self.material.name + " found.")
        
        # make sure product_material.unit is the same as emission_factor.unit
        if self.unit != emission_factor.unit:
            raise Exception("Unit mismatch: " + self.unit.name + " != " + emission_factor.unit.name)
        
        emmision_value = emission_factor.value * self.quantity
        emission_unit = emission_factor.unit
        return emmision_value, emission_unit
        

