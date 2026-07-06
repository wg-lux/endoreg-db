from __future__ import annotations
from django.db import models


class Report(models.Model):
    name: models.CharField[str] = models.CharField(max_length=100, unique=True)
