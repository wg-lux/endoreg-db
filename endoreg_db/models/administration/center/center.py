from typing import TYPE_CHECKING, cast

from django.db import models
from django.utils.text import slugify

if TYPE_CHECKING:
    from ...administration import CenterProduct, CenterResource, CenterWaste
    from ...media import AnonymExaminationReport, AnonymHistologyReport
    from ...medical import Endoscope, EndoscopyProcessor
    from ..person.names.first_name import FirstName
    from ..person.names.last_name import LastName


class CenterManager(models.Manager):
    def get_by_natural_key(self, name) -> "Center":
        return cast("Center", self.get(name=name))

    def get_by_center_key(self, center_key: str) -> "Center":
        return cast("Center", self.get(center_key=center_key))


class Center(models.Model):
    objects = CenterManager()
    name = models.CharField(max_length=255)
    center_key = models.CharField(max_length=255, unique=True, blank=True)
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
        def anonymexaminationreport_set(
            self,
        ) -> RelatedManager[AnonymExaminationReport]: ...

        @property
        def anonymhistologyreport_set(
            self,
        ) -> RelatedManager[AnonymHistologyReport]: ...

    @classmethod
    def get_by_name(cls, name):
        return cls.objects.get(name=name)

    @classmethod
    def get_by_center_key(cls, center_key: str):
        return cls.objects.get(center_key=center_key)

    @classmethod
    def resolve_identity(cls, identifier: str):
        return (
            cls.objects.filter(center_key=identifier).first()
            or cls.objects.filter(name=identifier).first()
        )

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    @classmethod
    def build_center_key(cls, value: str, *, exclude_pk: int | None = None) -> str:
        base = slugify(value or "") or "center"
        candidate = base
        suffix = 2
        queryset = cls.objects.all()
        if exclude_pk is not None:
            queryset = queryset.exclude(pk=exclude_pk)
        while queryset.filter(center_key=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(self, *args, **kwargs):
        if self.pk:
            existing_key = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("center_key", flat=True)
                .first()
            )
            if existing_key and self.center_key and self.center_key != existing_key:
                raise ValueError("center_key is immutable once assigned")

        if not self.center_key:
            source_value = self.display_name or self.name
            self.center_key = self.build_center_key(
                source_value,
                exclude_pk=self.pk,
            )
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
