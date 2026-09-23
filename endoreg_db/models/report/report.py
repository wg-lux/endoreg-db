from __future__ import annotations
from typing import Any
from django.db import models


class Report(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
