from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.hardware.endoscope import Endoscope
    from endoreg_db.models.persons.first_name import FirstName
    from endoreg_db.models.persons.last_name import LastName



class CenterManager(models.Manager):
    def get_by_natural_key(self, name):
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
        from endoreg_db.models.hardware.endoscopy_processor import EndoscopyProcessor

        endoscopy_processors: models.QuerySet["EndoscopyProcessor"]


    @classmethod
    def get_by_name(cls, name):
        return cls.objects.get(name=name)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(object=self.name)

    def get_first_names(self):
        from endoreg_db.models import FirstName

        names = FirstName.objects.filter(centers=self)
        return names

    def get_last_names(self):
        from endoreg_db.models import LastName

        names = LastName.objects.filter(centers=self)
        return names

    def get_endoscopes(self):
        from endoreg_db.models import Endoscope

        endoscopes = Endoscope.objects.filter(center=self)
        return endoscopes

    # def get_endoscopy_processor
