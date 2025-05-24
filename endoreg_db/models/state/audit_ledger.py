# stats/models.py
import hashlib, json, uuid
from django.db import models
from django.utils import timezone
from django.conf import settings

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


class AuditLedger(models.Model):
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ts           = models.DateTimeField(default=timezone.now, editable=False)
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    object_type  = models.CharField(max_length=80)            # e.g. 'VideoFile'
    object_pk    = models.CharField(max_length=40)            # UUID or int
    action       = models.CharField(max_length=40)            # 'created' | 'validated' | …
    data         = models.JSONField()                         # snapshot / diff / metadata
    prev_hash    = models.CharField(max_length=64, editable=False)
    hash         = models.CharField(max_length=64, editable=False)

    class Meta:
        ordering = ['ts']
        indexes  = [models.Index(fields=['object_type', 'object_pk'])]

    # ------------------------------------------------------
    def save(self, *args, **kw):
        """
        Saves a new immutable audit record, chaining hashes for integrity.
        
        Raises:
            RuntimeError: If an attempt is made to update an existing record.
        """
        if self._state.adding:                         # only on INSERT
            self.prev_hash = self._last_hash()
            self.hash      = self._compute_hash()
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
        last = AuditLedger.objects.order_by('-ts').first()
        return last.hash if last else '0' * 64

    def _compute_hash(self) -> str:
        """
        Computes a SHA-256 hash representing the current audit record's state.
        
        The hash is generated from a JSON payload containing the timestamp, user ID, object type and primary key, action, associated data, and the previous record's hash. This ensures cryptographic integrity and chaining of audit records.
        
        Returns:
            The hexadecimal SHA-256 hash string of the current record's payload.
        """
        payload = {
            'ts':   self.ts.isoformat(),
            'uid':  str(self.user_id),
            'obj':  f'{self.object_type}:{self.object_pk}',
            'act':  self.action,
            'data': self.data,
            'prev': self.prev_hash,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    
    def log_validation(user, instance, action: str, extra=None):
        """
        Creates a new audit ledger entry for a validation action performed on a model instance.
        
        Args:
            user: The user performing the action.
            instance: The model instance being validated.
            action: The action performed (e.g., 'validated', 'created').
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
        Returns the number of distinct object primary keys for a given object type and action.
        
        Args:
            object_type: The type of object to filter by.
            action: The action to filter by.
        
        Returns:
            The count of unique object primary keys matching the specified type and action.
        """
        return (
            AuditLedger.objects
            .filter(object_type=object_type, action=action)
            .values('object_pk')
            .distinct()
            .count()
        )

    def collect_counters():
        """
        Aggregates and returns summary counters for various tracked entities and actions.
        
        Returns:
            dict: A dictionary containing counts for cases, videos, annotations, anonymizations,
            images, and breakdowns of video statuses based on audit log entries.
        """
        return {
            "totalCases":           AuditLedger._distinct("VideoFile", "created"),
            "totalVideos":          AuditLedger._distinct("VideoFile", "created"),
            "totalAnnotations":     AuditLedger.objects.filter(action="annotation_added").count(),
            "totalAnonymizations":  AuditLedger._distinct("VideoFile", "anonymized"),
            "totalImages":          AuditLedger._distinct("Image",     "created"),
            # video breakdown
            "videosCompleted":      AuditLedger._distinct("VideoFile", "validated"),
            "videosAnonym":         AuditLedger._distinct("VideoFile", "anonymized"),
            # add more as needed …
        }
