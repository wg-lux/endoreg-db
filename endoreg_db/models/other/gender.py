from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models

if TYPE_CHECKING:
    from ..administration import Patient


class GenderManager(models.Manager["Gender"]):
    def get_by_natural_key(self, name: str) -> "Gender":
        gender = self.resolve_by_name(name)
        if gender is None:
            raise self.model.DoesNotExist(
                f"{self.model._meta.object_name} matching query does not exist."
            )
        return gender

    def resolve_by_name(
        self, name: str, *, case_insensitive: bool = True
    ) -> "Gender | None":
        normalized_name = str(name).strip()
        lookup = (
            {"name__iexact": normalized_name}
            if case_insensitive
            else {"name": normalized_name}
        )
        return self.filter(**lookup).order_by("pk").first()

    def get_or_create_by_name(
        self, name: str, *, defaults: dict[str, object] | None = None
    ) -> tuple["Gender", bool]:
        normalized_name = str(name).strip()
        gender = self.resolve_by_name(normalized_name)
        if gender is not None:
            return gender, False
        return self.create(name=normalized_name, **(defaults or {})), True


class Gender(models.Model):
    """A class representing gender."""

    objects = GenderManager()

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    abbreviation: models.CharField[Any, Any] = models.CharField(
        max_length=255, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)

    if TYPE_CHECKING:

        @property
        def patients(self) -> models.QuerySet["Patient"]: ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)
