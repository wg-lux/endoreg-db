from django.conf import settings
from django.db import models


class OperationLog(models.Model):
    """
    Lightweight log of user-triggered operations (audit-like).
    """

    # actor_id – internal Django user ID (primary key)
    # Who did it
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operation_logs",
    )
    actor_username = models.CharField(max_length=150, blank=True)
    actor_email = models.EmailField(blank=True)
    actor_keycloak_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Keycloak subject/ID if you later want to store it.",
    )

    # What happened
    action = models.CharField(
        max_length=100,
        help_text="e.g. 'anonymization.start', 'anonymization.validate'",
    )
    http_method = models.CharField(max_length=10, blank=True)
    path = models.CharField(max_length=512, blank=True)

    # On what resource
    resource_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g. 'video', 'pdf'",
    )
    resource_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="ID of VideoFile / RawPdfFile etc.",
    )

    # State before/after
    status_before = models.CharField(max_length=50, blank=True)
    status_after = models.CharField(max_length=50, blank=True)

    # Extra info
    meta = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    #
    class Meta:
        verbose_name = "Operation Log"
        verbose_name_plural = "Operation Logs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"[{self.created_at.isoformat()}] {self.action} by {self.actor_username or 'unknown'}"
