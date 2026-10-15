from typing import TYPE_CHECKING

from django.db import models


if TYPE_CHECKING:
    from lx_dtypes.models.contracts.contraindication import ContraindicationCore


class ContraindicationManager(models.Manager["Contraindication"]):
    def get_by_natural_key(self, name: str) -> "Contraindication":
        return self.get(name=name)


class Contraindication(models.Model):
    name: models.CharField[str, str] = models.CharField(max_length=100, unique=True)
    description: models.TextField[str | None, str | None] = models.TextField(
        blank=True,
        null=True,
    )

    objects = ContraindicationManager()

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
