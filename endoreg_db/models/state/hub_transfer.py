"""The sole Python failure-category mirror, checked against Rust at startup.

An absent failure is stored as an empty string for database compatibility; it is
not a failure-category member. Persistence and API schemas share this enum.
"""

from django.db import models


class HubExportFailureClass(models.TextChoices):
    CONFIGURATION_REJECTION = "configuration_rejection", "Configuration Rejection"
    AUTHORIZATION_DENIAL = "authorization_denial", "Authorization Denial"
    INTEGRITY_INCONSISTENCY = "integrity_inconsistency", "Integrity Inconsistency"
    TRANSIENT_RETRY = "transient_retry", "Transient Retry"
