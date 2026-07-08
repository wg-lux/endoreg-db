# state/audit_ledger.py
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.conf import settings
from django.db import models, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from lx_dtypes.models.contracts.audit_ledger import AuditLedgerHashPayload
from lx_dtypes.models.contracts.json_types import JsonObject

if TYPE_CHECKING:
    from django.contrib.auth.models import User


logger = logging.getLogger(__name__)


def _ledger_table_unavailable(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "auditledger" in message
        or "ledgerhead" in message
        or "no such table" in message
        or "does not exist" in message
    )


def _json_object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return cast(JsonObject, value)


class AuditLedger(models.Model):
    """
    Immutable audit ledger row.

    The cryptographic hash calculation is centralized in
    lx_dtypes.models.contracts.audit_ledger.AuditLedgerHashPayload.
    """

    objects: ClassVar[models.Manager["AuditLedger"]] = models.Manager()  # pyright: ignore[reportIncompatibleVariableOverride]

    id: models.UUIDField[Any, Any] = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    ts: models.DateTimeField[Any, Any] = models.DateTimeField(
        default=timezone.now,
        editable=False,
        db_index=True,
    )
    user: models.ForeignKey["User | None"] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    object_type: models.CharField[Any, Any] = models.CharField(max_length=80)
    object_pk: models.CharField[Any, Any] = models.CharField(max_length=40)
    action: models.CharField[Any, Any] = models.CharField(max_length=40)
    data: models.JSONField[Any, Any] = models.JSONField()
    prev_hash: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        editable=False,
    )
    hash: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        editable=False,
    )

    class Meta:
        ordering = ["ts"]
        indexes = [models.Index(fields=["object_type", "object_pk"])]

    def save(self, *args: object, **kwargs: object) -> None:
        """
        Save a new immutable audit record, computing and linking cryptographic hashes.

        Raises:
            RuntimeError: If an attempt is made to modify an existing audit record.
        """
        with transaction.atomic():
            try:
                if self._state.adding:
                    head = LedgerHead.lock()
                    object.__setattr__(self, "prev_hash", head.current_hash)
                    object.__setattr__(self, "hash", self._compute_hash())
                else:
                    raise RuntimeError("AuditLedger rows are immutable")

                super().save(*args, **kwargs)

                object.__setattr__(head, "current_hash", self.hash)
                object.__setattr__(head, "last_entry", self)
                head.save(update_fields=["current_hash", "last_entry", "updated_at"])
            except (OperationalError, ProgrammingError) as exc:
                if _ledger_table_unavailable(exc):
                    logger.warning(
                        "AuditLedger table unavailable; skipping audit write: %s",
                        exc,
                    )
                    return
                raise

    def _last_hash(self) -> str:
        """
        Return the current ledger head hash, or zero hash if no head exists.
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
        Verify every ledger row still matches its stored hash and previous link.
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
        user = getattr(self, "user", None)
        uid = None if user is None else str(getattr(user, "pk", None))

        payload = AuditLedgerHashPayload(
            ts=self.ts.isoformat(),
            id=str(self.id),
            uid=uid,
            obj=f"{self.object_type}:{self.object_pk}",
            act=self.action,
            data=_json_object(self.data),
            prev=self.prev_hash,
        )
        return payload.sha256_hex()

    def log_validation(
        self,
        user: models.Model | None,
        instance: models.Model,
        action: str,
        extra: JsonObject | None = None,
    ) -> None:
        """
        Create an audit record for a validation action.
        """
        AuditLedger.objects.create(
            user=cast(Any, user),
            object_type=instance.__class__.__name__,
            object_pk=str(instance.pk),
            action=action,
            data=extra or {},
        )

    @classmethod
    def append_identity_commit(
        cls,
        *,
        user: object | None = None,
        object_type: str,
        object_pk: str,
        data: JsonObject,
    ) -> "AuditLedger | None":
        """
        Append an immutable identity commit without requiring a user context.
        """
        try:
            audit_user = (
                cast(models.Model, user)
                if getattr(user, "is_authenticated", False)
                else None
            )
            return cls.objects.create(
                user=cast(Any, audit_user),
                object_type=object_type,
                object_pk=object_pk,
                action="identity_committed",
                data=data,
            )
        except (OperationalError, ProgrammingError) as exc:
            if _ledger_table_unavailable(exc):
                logger.warning(
                    "AuditLedger table unavailable; skipping identity commit: %s",
                    exc,
                )
                return None
            raise

    @classmethod
    def _distinct(cls, object_type: str, action: str) -> int:
        """
        Return the number of distinct objects for an audit action.
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

    def collect_counters(self) -> dict[str, int]:
        """
        Aggregate summary statistics for audit actions and object types.
        """
        return {
            "totalCases": AuditLedger._distinct("PatientExamination", "created"),
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
    """

    id: models.PositiveSmallIntegerField[Any, Any] = models.PositiveSmallIntegerField(
        primary_key=True,
        default=1,
        editable=False,
    )
    current_hash: models.CharField[Any, Any] = models.CharField(
        max_length=64,
        default="0" * 64,
        editable=False,
    )
    last_entry: models.ForeignKey["AuditLedger | None"] = models.ForeignKey(
        AuditLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        editable=False,
        related_name="+",
    )
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(
        auto_now=True,
    )

    objects: ClassVar[models.Manager["LedgerHead"]] = models.Manager()  # pyright: ignore[reportIncompatibleVariableOverride]

    if TYPE_CHECKING:
        last_entry_id: int | None

    class Meta:
        verbose_name = "Ledger Head"
        verbose_name_plural = "Ledger Heads"

    @classmethod
    def lock(cls) -> "LedgerHead":
        head, _created = cls.objects.get_or_create(pk=1)
        return cls.objects.select_for_update().get(pk=head.pk)
