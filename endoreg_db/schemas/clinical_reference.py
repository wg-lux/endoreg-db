"""Typed host projection and reconciliation of an immutable clinical graph."""

from enum import StrEnum
from typing import Literal, Self

from lx_dtypes.models.contracts.knowledge_base_graph import KnowledgeBaseGraphSnapshot
from lx_dtypes.models.contracts.json_types import JsonValue
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReferenceModel(StrEnum):
    UNIT = "Unit"
    FINDING_TYPE = "FindingType"
    INTERVENTION_TYPE = "FindingInterventionType"
    INTERVENTION = "FindingIntervention"
    CLASSIFICATION_TYPE = "FindingClassificationType"
    CLASSIFICATION = "FindingClassification"
    CHOICE = "FindingClassificationChoice"
    FINDING = "Finding"
    INDICATION_CLASSIFICATION = "ExaminationIndicationClassification"
    INDICATION_CHOICE = "ExaminationIndicationClassificationChoice"
    INDICATION = "ExaminationIndication"
    EXAMINATION_TYPE = "ExaminationType"
    EXAMINATION = "Examination"
    INFORMATION_SOURCE = "InformationSource"


class ReferenceKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: ReferenceModel
    name: str = Field(min_length=1)

    @property
    def identity(self) -> tuple[ReferenceModel, str]:
        return self.model, self.name


class ReferenceDefinition(ReferenceKey):
    # Only approved fields compiled from the shared graph may reach the ORM.
    attributes: dict[str, str | None] = Field(default_factory=dict)
    relations: dict[str, list[ReferenceKey]] = Field(default_factory=dict)


class ReferenceBinding(ReferenceKey):
    row_id: int = Field(gt=0)
    origin: Literal["created", "legacy_equivalent", "shared_import"]


class ClinicalReferenceReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    snapshot: KnowledgeBaseGraphSnapshot
    bindings: list[ReferenceBinding]

    @model_validator(mode="after")
    def unique_bindings(self) -> Self:
        keys = [binding.identity for binding in self.bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("clinical reference bindings must have unique identities")
        return self


class ReferenceDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    field: str
    existing: JsonValue
    proposed: JsonValue


class ReferenceChange(ReferenceKey):
    action: Literal["create", "reuse", "adopt", "conflict"]
    row_id: int | None = None
    differing_fields: list[str] = Field(default_factory=list)
    differences: list[ReferenceDifference] = Field(
        default_factory=lambda: list[ReferenceDifference]()
    )


class ClinicalReferencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    module: str
    version: str
    snapshot_id: str
    changes: list[ReferenceChange]
    conflicts: list[str] = Field(default_factory=list)
    unchanged: bool = False

    @property
    def can_import(self) -> bool:
        return not self.conflicts and all(c.action != "conflict" for c in self.changes)
