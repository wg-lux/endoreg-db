"""Specification and dry-run results for a bounded legacy catalogue migration."""

from __future__ import annotations

from typing import Literal, Self

from lx_dtypes.models.contracts.reference_catalog import ExaminationIndicationReference
from pydantic import BaseModel, ConfigDict, Field, model_validator

from endoreg_db.services.reference_data.catalog import CatalogPlan


class MigrationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["1.0"]
    migration_id: Literal["colorectal_indication_interventions_v1"]
    module: Literal["endoreg_reference"]
    version: Literal["1.0.0"]
    snapshot_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    indications: list[str] = Field(min_length=7, max_length=7)
    interventions: list[str] = Field(min_length=8, max_length=8)
    intervention_types: list[Literal["histology_sampling"]] = Field(
        min_length=1, max_length=1
    )

    @model_validator(mode="after")
    def unique_identities(self) -> Self:
        for names in (self.indications, self.interventions, self.intervention_types):
            if len(names) != len(set(names)):
                raise ValueError("Migration identities must be unique")
        return self


class IndicationRelationshipChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    row_id: int = Field(gt=0)
    before: ExaminationIndicationReference
    after: ExaminationIndicationReference


class MigrationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    reconciliation: CatalogPlan
    relationships: list[IndicationRelationshipChange]
    blockers: list[str]

    @property
    def can_apply(self) -> bool:
        return not self.blockers
