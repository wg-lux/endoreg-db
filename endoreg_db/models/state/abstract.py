from django.db import models


class AbstractState(models.Model):
    """Abstract base class for all states."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
