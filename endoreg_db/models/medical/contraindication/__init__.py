from __future__ import annotations
from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager


if TYPE_CHECKING:
    from lx_dtypes.models.contracts.contraindication import ContraindicationCore


class Contraindication(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=100, unique=True)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[Any, Any] = models.TextField(
        blank=True,
        null=True,
    )

    objects = NameNaturalKeyManager["Contraindication"]()

    if TYPE_CHECKING:
        from endoreg_db.models import FindingIntervention

        @property
        def contraindicating_finding_interventions(
            self,
        ) -> "models.Manager[FindingIntervention]": ...

        def to_core_concept(self) -> "ContraindicationCore": ...

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)
