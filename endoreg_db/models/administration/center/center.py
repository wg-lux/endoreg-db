from typing import TYPE_CHECKING, cast

from django.db import models

if TYPE_CHECKING:
    from ...administration import CenterProduct, CenterResource, CenterWaste
    from ...media import AnonymExaminationReport, AnonymHistologyReport
    from ...medical import Endoscope, EndoscopyProcessor
    from ..person.names.first_name import FirstName
    from ..person.names.last_name import LastName


class CenterManager(models.Manager):
    def get_by_natural_key(self, name) -> "Center":
        return self.get(name=name)


class Center(models.Model):
    objects = CenterManager()
    name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True, default="")

    first_names = models.ManyToManyField(
        to="FirstName",
        related_name="centers",
    )
    last_names = models.ManyToManyField("LastName", related_name="centers")

    if TYPE_CHECKING:
        from django.db.models.manager import RelatedManager

        first_names = cast(RelatedManager[FirstName], first_names)
        last_names = cast(RelatedManager[LastName], last_names)

        @property
        def center_products(self) -> RelatedManager[CenterProduct]: ...

        @property
        def center_resources(self) -> RelatedManager[CenterResource]: ...

        @property
        def center_wastes(self) -> RelatedManager[CenterWaste]: ...

        @property
        def endoscopy_processors(self) -> RelatedManager[EndoscopyProcessor]: ...

        @property
        def endoscopes(self) -> RelatedManager[Endoscope]: ...

        @property
        def anonymexaminationreport_set(self) -> RelatedManager[AnonymExaminationReport]: ...

        @property
        def anonymhistologyreport_set(self) -> RelatedManager[AnonymHistologyReport]: ...

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
        return str(self.display_name or self.name)

    def get_first_names(self):
        return self.first_names.all()

    def get_last_names(self):
        return self.last_names.all()

    def get_endoscopes(self):
        """
        Returns all Endoscope instances associated with this center.
        """
        return self.endoscopes.all()
