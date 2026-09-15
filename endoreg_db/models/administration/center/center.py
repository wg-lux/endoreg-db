from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias, Unpack, cast, Any

from django.db import models
from django.utils.text import slugify

from endoreg_db.helpers.typing import DjangoModelSaveKwargs

if TYPE_CHECKING:
    from ...administration import CenterProduct, CenterResource, CenterWaste
    from ...media import AnonymExaminationReport, AnonymHistologyReport
    from ...medical import Endoscope, EndoscopyProcessor
    from ..person.names.first_name import FirstName
    from ..person.names.last_name import LastName


NoCenterSaveValue: TypeAlias = None
CenterPk: TypeAlias = int | NoCenterSaveValue


class CenterManager(models.Manager["Center"]):
    def get_by_natural_key(self, name: str) -> "Center":
        return self.get(name=name)

    def get_by_center_key(self, center_key: str) -> "Center":
        return self.get(center_key=center_key)


class Center(models.Model):
    objects = CenterManager()
    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    center_key: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        unique=True,
        blank=True,
    )
    display_name: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    first_names: models.ManyToManyField[FirstName, FirstName] = models.ManyToManyField(
        to="FirstName",
        related_name="centers",
    )
    last_names: models.ManyToManyField[LastName, LastName] = models.ManyToManyField(
        "LastName",
        related_name="centers",
    )

    if TYPE_CHECKING:

        @property
        def center_products(self) -> models.Manager[CenterProduct]: ...

        @property
        def center_resources(self) -> models.Manager[CenterResource]: ...

        @property
        def center_wastes(self) -> models.Manager[CenterWaste]: ...

        @property
        def endoscopy_processors(self) -> models.Manager[EndoscopyProcessor]: ...

        @property
        def endoscopes(self) -> models.Manager[Endoscope]: ...

        @property
        def anonymexaminationreport_set(
            self,
        ) -> models.Manager[AnonymExaminationReport]: ...

        @property
        def anonymhistologyreport_set(
            self,
        ) -> models.Manager[AnonymHistologyReport]: ...

    @classmethod
    def get_by_name(cls, name: str) -> "Center":
        return cls.objects.get(name=name)

    @classmethod
    def get_by_center_key(cls, center_key: str) -> "Center":
        return cls.objects.get(center_key=center_key)

    @classmethod
    def resolve_identity(cls, identifier: str) -> "Center | NoCenterSaveValue":
        return (
            cls.objects.filter(center_key=identifier).first()
            or cls.objects.filter(name=identifier).first()
        )

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    @classmethod
    def build_center_key(
        cls,
        value: str,
        *,
        exclude_pk: CenterPk = None,
    ) -> str:
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

    def save(self, **kwargs: Unpack[DjangoModelSaveKwargs]) -> None:
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
                exclude_pk=cast(CenterPk, self.pk),
            )
        if not self.display_name:
            self.display_name = self.name
        super().save(**kwargs)

    def __str__(self) -> str:
        return str(self.display_name or self.name)

    def get_first_names(self) -> models.QuerySet[FirstName]:
        return self.first_names.all()

    def get_last_names(self) -> models.QuerySet[LastName]:
        return self.last_names.all()

    def get_endoscopes(self) -> models.QuerySet[Endoscope]:
        """
        Returns all Endoscope instances associated with this center.
        """
        return self.endoscopes.all()
