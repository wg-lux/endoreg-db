from django.db import models
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.db.models.manager import Manager
    from django.db.models.query import QuerySet
    from ...medical import Endoscope, EndoscopyProcessor
    from ...administration import (
        FirstName, LastName,
        CenterProduct,
        CenterWaste, CenterResource
    )
    from ...media import AnonymExaminationReport, AnonymHistologyReport

# pylint: disable=too-few-public-methods
class CenterManager(models.Manager["Center"]):
    def get_by_natural_key(self, name: str) -> "Center":
        return self.get(name=name)


class Center(models.Model):
    # The 'objects' manager is intentionally overridden for custom methods.
    # Type checker warnings about overriding are noted but standard in Django.
    # pylint: disable=invalid-name
    objects: "CenterManager" = CenterManager()

    # Model fields with class-level type hints
    name: models.CharField = models.CharField(max_length=255)
    name_de: models.CharField = models.CharField(max_length=255, blank=True, null=True)
    name_en: models.CharField = models.CharField(max_length=255, blank=True, null=True)

    first_names: models.ManyToManyField = models.ManyToManyField(
        to="FirstName",
        related_name="centers",
    )
    last_names: models.ManyToManyField = models.ManyToManyField(
        "LastName",
        related_name="centers"
    )

    if TYPE_CHECKING:
        # Instance attribute types for CharFields and ManyToManyFields are inferred
        # by django-stubs from the class-level definitions above.
        # Only declare types here for reverse relations or other dynamic attributes.

        # Reverse relations are RelatedManagers on an instance
        center_products: Manager["CenterProduct"]
        center_resources: Manager["CenterResource"]
        center_wastes: Manager["CenterWaste"]
        endoscopy_processors: Manager["EndoscopyProcessor"]
        endoscopes: Manager["Endoscope"]  # Assumes 'endoscopes' is a reverse relation manager
        anonymexaminationreport_set: Manager["AnonymExaminationReport"]
        anonymhistologyreport_set: Manager["AnonymHistologyReport"]
        

    @classmethod
    def get_by_name(cls, name: str) -> "Center":
        return cls.objects.get(name=name)

    def natural_key(self) -> tuple[str, ...]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)

    def get_first_names(self) -> "QuerySet[FirstName]":
        # django-stubs should infer self.first_names as a RelatedManager
        # whose .all() method returns a QuerySet[FirstName]
        return self.first_names.all()

    def get_last_names(self) -> "QuerySet[LastName]":
        return self.last_names.all()

    def get_endoscopes(self) -> "QuerySet[Endoscope]":
        # Assumes self.endoscopes is a related manager as hinted in TYPE_CHECKING
        return self.endoscopes.all()

# Ensure single trailing newline (handled by tool if it adds one, or verify manually)


