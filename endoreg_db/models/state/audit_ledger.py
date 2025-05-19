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
        if self._state.adding:                         # only on INSERT
            self.prev_hash = self._last_hash()
            self.hash      = self._compute_hash()
        else:
            raise RuntimeError("AuditLedger rows are immutable")
        super().save(*args, **kw)

    # ------------------------------------------------------
    def _last_hash(self) -> str:
        last = AuditLedger.objects.order_by('-ts').first()
        return last.hash if last else '0' * 64

    def _compute_hash(self) -> str:
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
        AuditLedger.objects.create(
            user=user,
            object_type=instance.__class__.__name__,
            object_pk=str(instance.pk),
            action=action,
            data=extra or {},
        )

    def _distinct(object_type: str, action: str):
        return (
            AuditLedger.objects
            .filter(object_type=object_type, action=action)
            .values('object_pk')
            .distinct()
            .count()
        )

    def collect_counters():
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
