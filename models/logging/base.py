# django model for logging, first define an abstract base model
from django.db import models
from django.conf import settings

class AbstractLogEntry(models.Model):
    """
    Abstract base model for log entries.
    """

    class Meta:
        abstract = True

    timestamp = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, null=True, blank=True)
    message = models.TextField()
    json_content = models.JSONField(null=True, blank=True)
    

    def __str__(self):
        return f"{self.timestamp} - {self.user} - {self.ip_address} - {self.message}"