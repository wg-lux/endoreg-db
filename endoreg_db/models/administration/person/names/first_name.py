# class to represent unique first-names
# name attribute is natural key
from __future__ import annotations
from typing import Any
from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class FirstName(models.Model):
    objects = NameNaturalKeyManager["FirstName"]()
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
