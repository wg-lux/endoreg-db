from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias, Any

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils.text import slugify

if TYPE_CHECKING:
    from endoreg_db.models.administration.center.center import Center

NoNetworkNodeCenterValue: TypeAlias = NoneType
NetworkNodeCenter: TypeAlias = "Center | NoNetworkNodeCenterValue"


class NetworkNodeManager(models.Manager["NetworkNode"]):
    def get_by_node_key(self, node_key: str) -> "NetworkNode":
        return self.get(node_key=node_key)


class NetworkNode(models.Model):
    class Role(models.TextChoices):
        CENTRAL_HUB = "central_hub", "Central Hub"
        SITE_NODE = "site_node", "Site Node"
        STANDALONE = "standalone", "Standalone"

    objects = NetworkNodeManager()

    node_key: models.CharField[Any, Any] = models.CharField(
        max_length=255, unique=True, blank=True
    )
    display_name: models.CharField[Any, Any] = models.CharField(max_length=255)
    role: models.CharField[Any, Any] = models.CharField(
        max_length=32,
        choices=Role.choices,
        default=Role.SITE_NODE,
    )
    base_url: models.URLField[Any, Any] = models.URLField(blank=True, default="")
    is_active: models.BooleanField[Any, Any] = models.BooleanField(default=True)
    shared_secret_hash: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, default=""
    )
    owning_center: models.ForeignKey["NetworkNodeCenter"] = models.ForeignKey(
        "Center",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="network_nodes",
    )
    created_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[Any, Any] = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name", "pk"]

    @classmethod
    def build_node_key(cls, value: str, *, exclude_pk: int | None = None) -> str:
        base = slugify(value or "") or "node"
        candidate = base
        suffix = 2
        queryset = cls.objects.all()
        if exclude_pk is not None:
            queryset = queryset.exclude(pk=exclude_pk)
        while queryset.filter(node_key=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(self, *args: object, **kwargs: object) -> None:
        if self.pk:
            existing_key = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("node_key", flat=True)
                .first()
            )
            if existing_key and self.node_key and self.node_key != existing_key:
                raise ValueError("node_key is immutable once assigned")

        if not self.node_key:
            self.node_key = self.build_node_key(
                self.display_name,
                exclude_pk=self.pk,
            )

        super().save(*args, **kwargs)

    def set_shared_secret(self, secret: str) -> None:
        normalized = str(secret or "").strip()
        if not normalized:
            raise ValueError("shared secret must not be empty")
        self.shared_secret_hash = make_password(normalized)

    def check_shared_secret(self, secret: str) -> bool:
        normalized = str(secret or "").strip()
        if not normalized or not self.shared_secret_hash:
            return False
        return bool(check_password(normalized, self.shared_secret_hash))

    def __str__(self) -> str:
        return self.display_name
