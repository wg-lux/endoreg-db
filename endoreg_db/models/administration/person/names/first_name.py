# class to represent unique first-names
# name attribute is natural key
from __future__ import annotations
from django.db import models


class FirstNameManager(models.Manager["FirstName"]):
    def get_by_natural_key(self, name: str) -> "FirstName":
        return self.get(name=name)


class FirstName(models.Model):
    objects = FirstNameManager()
    name: models.CharField[str] = models.CharField(max_length=255, unique=True)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
