from django.db import models
from typing import TYPE_CHECKING

from endoreg_db.models.administration.person.names.first_name import FirstName
from endoreg_db.models.administration.person.names.last_name import LastName
from endoreg_db.models.medical.hardware.endoscope import Endoscope

if TYPE_CHECKING:
    from ...medical.hardware import Endoscope, EndoscopyProcessor
    from ..person import FirstName, LastName
    from .center_product import CenterProduct
    from .center_resource import CenterResource
    from .center_waste import CenterWaste

class CenterManager(models.Manager):
    def get_by_natural_key(self, name) -> "Center":
        return self.get(name=name)


class Center(models.Model):
    objects = CenterManager()

    # import_id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)

    first_names = models.ManyToManyField(
        to="FirstName",
        related_name="centers",
    )
    last_names = models.ManyToManyField("LastName", related_name="centers")

    if TYPE_CHECKING:
        center_products: models.QuerySet["CenterProduct"]
        center_resources: models.QuerySet["CenterResource"]
        center_wastes: models.QuerySet["CenterWaste"]
        endoscopy_processors: models.QuerySet["EndoscopyProcessor"]
        endoscopes: models.QuerySet["Endoscope"]
        first_names: models.QuerySet["FirstName"]
        last_names: models.QuerySet["LastName"]
        objects: CenterManager

    @classmethod
    def get_by_name(cls, name):
        return cls.objects.get(name=name)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(object=self.name)

    def get_first_names(self) -> models.BaseManager[FirstName]:
        from ..person import FirstName

        names = FirstName.objects.filter(centers=self)
        return names

    def get_last_names(self) -> models.BaseManager[LastName]:
        from ..person import LastName

        names = LastName.objects.filter(centers=self)
        return names

    def get_endoscopes(self) -> models.BaseManager[Endoscope]:
        from endoreg_db.models.medical.hardware import Endoscope

        endoscopes = Endoscope.objects.filter(center=self)
        return endoscopes

    
