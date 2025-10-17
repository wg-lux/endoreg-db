from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...medical import Endoscope, EndoscopyProcessor
    from ...administration import (
        CenterProduct,
        CenterWaste, CenterResource
    )
    from ...media import AnonymExaminationReport, AnonymHistologyReport

class CenterManager(models.Manager):
    def get_by_natural_key(self, name) -> "Center":
        return self.get(name=name)


class Center(models.Model):
    objects = CenterManager()

    # import_id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True, default="")

    first_names = models.ManyToManyField(
        to="FirstName",
        related_name="centers",
    )
    last_names = models.ManyToManyField("LastName", related_name="centers")

    if TYPE_CHECKING:
    #     first_names: RelatedManager["FirstName"]
    #     last_names: RelatedManager["LastName"]
        center_products: models.QuerySet["CenterProduct"]
        center_resources: models.QuerySet["CenterResource"]
        center_wastes: models.QuerySet["CenterWaste"]
        endoscopy_processors: models.QuerySet["EndoscopyProcessor"]
        endoscopes: models.QuerySet["Endoscope"]
        anonymexaminationreport_set: models.QuerySet["AnonymExaminationReport"]
        anonymhistologyreport_set: models.QuerySet["AnonymHistologyReport"]
        

    @classmethod
    def get_by_name(cls, name):
        return cls.objects.get(name=name)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = self.name
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return str(object=self.display_name or self.name)

    def get_first_names(self):
        return self.first_names.all()

    def get_last_names(self):
        return self.last_names.all()

    def get_endoscopes(self):
        """
        Returns all Endoscope instances associated with this center.
        """
        return self.endoscopes.all()
