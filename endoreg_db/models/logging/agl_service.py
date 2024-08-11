from django.db import models
from .base import AbstractLogEntry

class AglServiceLogEntry(AbstractLogEntry):
    """
    Model for log entries related to AGL services.
    """
    service = models.ForeignKey("AglService", on_delete=models.CASCADE)

    class Meta:
        verbose_name = "AGL Service Log Entry"
        verbose_name_plural = "AGL Service Log Entries"

    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.ip_address} - {self.service} - {self.message}"
