from django.db import models
from .base import AbstractLogEntry

class NetworkDeviceLogEntry(AbstractLogEntry):
    """
    Model for log entries related to network devices.
    """
    device = models.ForeignKey("NetworkDevice", on_delete=models.CASCADE)
    log_type = models.ForeignKey("LogType", on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        verbose_name = "Network Device Log Entry"
        verbose_name_plural = "Network Device Log Entries"

    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.ip_address} - {self.device} - {self.message}"

