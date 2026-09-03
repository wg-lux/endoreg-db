from __future__ import annotations
from typing import Any

from django.db import models


class AbstractState(models.Model):
    """Abstract base class for all states."""

    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
