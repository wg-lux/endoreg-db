from django.db import models
from .base import AbstractLogEntry

class NetworkDeviceLogEntry(AbstractLogEntry):
    """
    Model for log entries related to network devices.
    """
    device = models.ForeignKey("NetworkDevice", on_delete=models.CASCADE)
    log_type = models.ForeignKey("LogType", on_delete=models.CASCADE, null=True, blank=True)

    datetime = models.DateTimeField(default=None, null=True, blank=True)
    # hostname = models.CharField(max_length=255, null=True, blank=True)
    aglnet_ip = models.GenericIPAddressField(null=True, blank=True)
    vpn_service_status = models.CharField(max_length=255, null=True, blank=True)
    vpn_service_restart_attempt = models.BooleanField(default=False)
    vpn_service_restart_success = models.BooleanField(null=True, blank=True)
    ping_vpn = models.BooleanField(default=False)
    ping_www = models.BooleanField(default=False)
    transferred_to_host = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Network Device Log Entry"
        verbose_name_plural = "Network Device Log Entries"

    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.ip_address} - {self.device} - {self.message}"

