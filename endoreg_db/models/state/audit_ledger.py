# stats/models.py
import hashlib
import json
import uuid

from django.conf import settings
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from django.db import transaction
import logging

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

logger = logging.getLogger(__name__)


def _ledger_table_unavailable(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "auditledger" in message
        or "ledgerhead" in message
        or "no such table" in message
        or "does not exist" in message
    )


class AuditLedger(models.Model):
    objects: "models.Manager[AuditLedger]" = models.Manager()
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ts = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
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
        with transaction.atomic():
            try:
                if self._state.adding:  # only on INSERT
                    head = LedgerHead.lock()
                    self.prev_hash = head.current_hash
                    self.hash = self._compute_hash()
                else:
                    raise RuntimeError("AuditLedger rows are immutable")
                super().save(*args, **kw)
                head.current_hash = self.hash
                head.last_entry = self
                head.save(update_fields=["current_hash", "last_entry", "updated_at"])
            except (OperationalError, ProgrammingError) as exc:
                if _ledger_table_unavailable(exc):
                    logger.warning(
                        "AuditLedger table unavailable; skipping audit write: %s", exc
                    )
                    return
                raise

    # ------------------------------------------------------
    def _last_hash(self) -> str:
        """
        Retrieves the hash of the most recent audit record.

        Returns:
            The SHA-256 hash of the latest `AuditLedger` entry by timestamp, or a string of 64 zeros if no records exist.
        """
        try:
            head = LedgerHead.objects.first()
            return head.current_hash if head else "0" * 64
        except (OperationalError, ProgrammingError) as exc:
            if _ledger_table_unavailable(exc):
                logger.warning(
                    "AuditLedger table unavailable while reading last hash; using zero hash."
                )
                return "0" * 64
            raise

    @classmethod
    def verify_chain(cls) -> bool:
        """
        Verify that every ledger row still matches its stored hash and previous link.

        The final hash is compared with LedgerHead as well, so deleting the most
        recent row is detectable even though no following row can reference it.
        """
        expected_prev_hash = "0" * 64
        for record in cls.objects.order_by("ts", "id").iterator():
            if record.prev_hash != expected_prev_hash:
                return False
            if record.hash != record._compute_hash():
                return False
            expected_prev_hash = record.hash

        head = LedgerHead.objects.first()
        head_hash = head.current_hash if head else "0" * 64
        return head_hash == expected_prev_hash

    def _compute_hash(self) -> str:
        """
        Computes the SHA-256 hash of the current audit record's data.

        The hash is generated from a JSON-serialized payload containing the timestamp, user ID, object type and primary key, action, associated data, and the previous record's hash. This ensures the integrity and immutability of the audit trail.

        Returns:
            The hexadecimal SHA-256 hash string representing the current audit record.
        """
        payload = {
            "ts": self.ts.isoformat(),
            "id": str(self.id),
            "uid": str(self.user_id) if self.user_id is not None else None,
            "obj": f"{self.object_type}:{self.object_pk}",
            "act": self.action,
            "data": self.data,
            "prev": self.prev_hash,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def log_validation(self, user, instance, action: str, extra=None):
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

    @classmethod
    def append_identity_commit(
        cls,
        *,
        user=None,
        object_type: str,
        object_pk: str,
        data: dict,
    ) -> "AuditLedger | None":
        """
        Append an immutable identity commit without requiring a user context.

        The caller must pass only non-PII identity metadata, typically hashes and
        object ids. The ledger hash-chain then makes later tampering detectable.
        """
        try:
            return cls.objects.create(
                user=user if getattr(user, "is_authenticated", False) else None,
                object_type=object_type,
                object_pk=object_pk,
                action="identity_committed",
                data=data,
            )
        except (OperationalError, ProgrammingError) as exc:
            if _ledger_table_unavailable(exc):
                logger.warning(
                    "AuditLedger table unavailable; skipping identity commit: %s", exc
                )
                return None
            raise

    @classmethod
    def _distinct(self, object_type: str, action: str):
        """
        Returns the number of distinct objects of a given type that have a specific audit action recorded.

        Args:
            object_type: The type of object to filter by (e.g., 'VideoFile').
            action: The audit action to filter by (e.g., 'validated').

        Returns:
            The count of unique object primary keys matching the specified type and action.
        """
        try:
            return (
                AuditLedger.objects.filter(object_type=object_type, action=action)
                .values("object_pk")
                .distinct()
                .count()
            )
        except (OperationalError, ProgrammingError) as exc:
            if _ledger_table_unavailable(exc):
                logger.warning(
                    "AuditLedger table unavailable while collecting counters; returning 0."
                )
                return 0
            raise

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
            "totalAnnotations": AuditLedger.objects.filter(
                action="annotation_added"
            ).count(),
            "totalAnonymizations": AuditLedger._distinct("VideoFile", "anonymized"),
            "totalImages": AuditLedger._distinct("Image", "created"),
            "videosCompleted": AuditLedger._distinct("VideoFile", "validated"),
            "videosAnonym": AuditLedger._distinct("VideoFile", "anonymized"),
        }


class LedgerHead(models.Model):
    """
    Singleton pointer to the current AuditLedger hash.

    Writers lock this row before appending, preventing concurrent ledger forks.
    This avoids scanning the full ledger for every append and provides a lock
    target that serializes concurrent writers.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    current_hash = models.CharField(max_length=64, default="0" * 64, editable=False)
    last_entry = models.ForeignKey(
        AuditLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        editable=False,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects: "models.Manager[LedgerHead]" = models.Manager()

    class Meta:
        verbose_name = "Ledger Head"
        verbose_name_plural = "Ledger Heads"

    @classmethod
    def lock(cls) -> "LedgerHead":
        head, _created = cls.objects.get_or_create(pk=1)
        return cls.objects.select_for_update().get(pk=head.pk)
