# stats/models.py
import hashlib
import json
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

"""

AuditLedger Model

AuditLedger is a model that tracks changes to other models in the database.
It stores a hash of the previous state, the current state, and the action taken.
This allows for a complete audit trail of changes made to the database.
The model includes fields for the timestamp, user who made the change, object type,
object primary key, action taken, and the data associated with the change.
The save method computes the hash of the current state and the previous state
before saving the record to the database.
The hash is computed using SHA-256 and includes the timestamp, user ID,
object type, object primary key, action taken, and the data associated with the change.
The hash is stored in the database to ensure data integrity and to allow for
verification of the data.
The model also includes a method to retrieve the last hash from the database
to ensure that the current hash is always based on the most recent state of the database.
The model is designed to be immutable, meaning that once a record is created,
it cannot be modified. This ensures that the audit trail is complete and accurate.

Raises:
    RuntimeError: _description_

Returns:
    _type_: _description_
"""

# TODO implement later on


class AuditLedger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ts = models.DateTimeField(default=timezone.now, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    object_type = models.CharField(max_length=80)  # e.g. 'VideoFile'
    object_pk = models.CharField(max_length=40)  # UUID or int
    action = models.CharField(max_length=40)  # 'created' | 'validated' | …
    data = models.JSONField()  # snapshot / diff / metadata
    prev_hash = models.CharField(max_length=64, editable=False)
    hash = models.CharField(max_length=64, editable=False)

    class Meta:
        ordering = ["ts"]
        indexes = [models.Index(fields=["object_type", "object_pk"])]

    # ------------------------------------------------------
    def save(self, *args, **kw):
        """
        Saves a new immutable audit record, computing and linking cryptographic hashes.

        Raises:
            RuntimeError: If an attempt is made to modify an existing audit record.
        """
        if self._state.adding:  # only on INSERT
            self.prev_hash = self._last_hash()
            self.hash = self._compute_hash()
        else:
            raise RuntimeError("AuditLedger rows are immutable")
        super().save(*args, **kw)

    # ------------------------------------------------------
    def _last_hash(self) -> str:
        """
        Retrieves the hash of the most recent audit record.

        Returns:
            The SHA-256 hash of the latest `AuditLedger` entry by timestamp, or a string of 64 zeros if no records exist.
        """
        last = AuditLedger.objects.order_by("-ts").first()
        return last.hash if last else "0" * 64

    def _compute_hash(self) -> str:
        """
        Computes the SHA-256 hash of the current audit record's data.

        The hash is generated from a JSON-serialized payload containing the timestamp, user ID, object type and primary key, action, associated data, and the previous record's hash. This ensures the integrity and immutability of the audit trail.

        Returns:
            The hexadecimal SHA-256 hash string representing the current audit record.
        """
        payload = {
            "ts": self.ts.isoformat(),
            "uid": str(self.user_id),
            "obj": f"{self.object_type}:{self.object_pk}",
            "act": self.action,
            "data": self.data,
            "prev": self.prev_hash,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def log_validation(user, instance, action: str, extra=None):
        """
        Creates an audit record for a validation action performed by a user on a specific model instance.

        Args:
            user: The user performing the action.
            instance: The model instance being validated.
            action: The action performed (e.g., 'validated').
            extra: Optional additional data to include in the audit record.
        """
        AuditLedger.objects.create(
            user=user,
            object_type=instance.__class__.__name__,
            object_pk=str(instance.pk),
            action=action,
            data=extra or {},
        )

    def _distinct(object_type: str, action: str):
        """
        Returns the number of distinct objects of a given type that have a specific audit action recorded.

        Args:
            object_type: The type of object to filter by (e.g., 'VideoFile').
            action: The audit action to filter by (e.g., 'validated').

        Returns:
            The count of unique object primary keys matching the specified type and action.
        """
        return AuditLedger.objects.filter(object_type=object_type, action=action).values("object_pk").distinct().count()

    def collect_counters(self):
        """
        Aggregates and returns summary statistics for audit actions and object types.

        Returns:
            dict: A dictionary containing counts of distinct cases, videos, annotations,
            anonymizations, images, and breakdowns of video statuses based on audit records.
        """
        return {
            "totalCases": AuditLedger._distinct("VideoFile", "created"),
            "totalVideos": AuditLedger._distinct("VideoFile", "created"),
            "totalAnnotations": AuditLedger.objects.filter(action="annotation_added").count(),
            "totalAnonymizations": AuditLedger._distinct("VideoFile", "anonymized"),
            "totalImages": AuditLedger._distinct("Image", "created"),
            # video breakdown
            "videosCompleted": AuditLedger._distinct("VideoFile", "validated"),
            "videosAnonym": AuditLedger._distinct("VideoFile", "anonymized"),
            # add more as needed …
        }
