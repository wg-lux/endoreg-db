from django.db import models
    
class CenterWaste(models.Model):
    center = models.ForeignKey("Center", on_delete=models.CASCADE)
    year = models.IntegerField()
    waste = models.ForeignKey("Waste", on_delete=models.CASCADE)
    quantity = models.FloatField()
    unit = models.ForeignKey("Unit", on_delete=models.SET_NULL, null=True)
    emission_factor = models.ForeignKey("EmissionFactor", on_delete=models.SET_NULL, null=True)

    
    def __str__(self):
        result = f"{self.quantity} {self.unit}"
        result += f" -\t{self.waste}, EmissionFactor: {self.emission_factor}\t\t- {self.center} - {self.year}"

        return result
