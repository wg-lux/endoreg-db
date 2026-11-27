from __future__ import annotations
from typing import Any
from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


class LastName(models.Model):
    objects = NameNaturalKeyManager["LastName"]()
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)


# Path: endoreg_db/models/persons/first_name.py
