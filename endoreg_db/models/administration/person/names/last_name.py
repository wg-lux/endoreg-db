from __future__ import annotations
from typing import Any
from django.db import models


class LastNameManager(models.Manager["LastName"]):
    def get_by_natural_key(self, name: str) -> "LastName":
        return self.get(name=name)


class LastName(models.Model):
    objects = LastNameManager()
    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)


# Path: endoreg_db/models/persons/first_name.py
