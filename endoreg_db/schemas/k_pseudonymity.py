from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UtilityFeatureKind(StrEnum):
    CATEGORICAL = "categorical"
    CONTINUOUS = "continuous"


class SensitiveAttributeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    allowed_values: tuple[str, ...] = Field(min_length=1)
    l_diversity: int | None = Field(default=None, ge=2)
    t_closeness: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_allowed_values(self) -> "SensitiveAttributeConfig":
        _require_unique(f"allowed_values for {self.name}", self.allowed_values)
        if any(not value for value in self.allowed_values):
            raise ValueError("allowed sensitive values must not be empty")
        if self.l_diversity is not None and self.l_diversity > len(self.allowed_values):
            raise ValueError(
                f"l_diversity for {self.name} exceeds its allowed value domain"
            )
        return self


class UtilityFeatureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    kind: UtilityFeatureKind
    weight: float = Field(gt=0.0, le=1.0)
    normalization_scale: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def validate_scale(self) -> "UtilityFeatureConfig":
        if self.kind is UtilityFeatureKind.CONTINUOUS:
            if self.normalization_scale is None:
                raise ValueError(
                    "continuous utility features require normalization_scale"
                )
        elif self.normalization_scale is not None:
            raise ValueError(
                "normalization_scale is only valid for continuous utility features"
            )
        return self


class RepairCostWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    size: float = Field(default=1.0, ge=0.0)
    sensitive_changes: float = Field(default=1.0, ge=0.0)
    distribution: float = Field(default=1.0, ge=0.0)

    @model_validator(mode="after")
    def require_nonzero_weight(self) -> "RepairCostWeights":
        if self.size + self.sensitive_changes + self.distribution <= 0.0:
            raise ValueError("at least one repair cost weight must be positive")
        return self


class KPseudonymityReleaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["1.0"] = "1.0"
    release_columns: tuple[str, ...] = Field(min_length=1)
    quasi_identifiers: tuple[str, ...] = Field(min_length=1, max_length=10)
    sensitive_attributes: tuple[SensitiveAttributeConfig, ...] = Field(min_length=1)
    utility_features: tuple[UtilityFeatureConfig, ...] = Field(min_length=1)
    k: int = Field(ge=2)
    tau_max: float = Field(ge=0.0)
    max_synthetic_rows: int = Field(ge=0, le=100_000)
    max_state_evaluations: int = Field(default=10_000, ge=1, le=1_000_000)
    max_candidate_combinations: int = Field(default=10_000, ge=1, le=100_000)
    max_input_rows: int = Field(default=100_000, ge=1, le=1_000_000)
    missing_value_token: str = Field(default="__MISSING__", min_length=1)
    synthetic_rows_count_toward_k: bool = False
    recipient_can_observe_synthetic_provenance: bool = True
    include_projection_diagnostics: bool = True
    repair_cost_weights: RepairCostWeights = Field(default_factory=RepairCostWeights)

    @model_validator(mode="after")
    def validate_release_contract(self) -> "KPseudonymityReleaseConfig":
        _require_unique("release_columns", self.release_columns)
        _require_unique("quasi_identifiers", self.quasi_identifiers)
        sensitive_names = tuple(item.name for item in self.sensitive_attributes)
        utility_names = tuple(item.name for item in self.utility_features)
        _require_unique("sensitive_attributes", sensitive_names)
        _require_unique("utility_features", utility_names)

        release_columns = set(self.release_columns)
        missing_columns = (
            set(self.quasi_identifiers) | set(sensitive_names) | set(utility_names)
        ) - release_columns
        if missing_columns:
            raise ValueError(
                "configured fields missing from release_columns: "
                + ", ".join(sorted(missing_columns))
            )
        overlap = set(self.quasi_identifiers) & set(sensitive_names)
        if overlap:
            raise ValueError(
                "quasi-identifiers and sensitive attributes must be disjoint: "
                + ", ".join(sorted(overlap))
            )
        forbidden = sorted(
            column
            for column in release_columns
            if column.casefold() in FORBIDDEN_DIRECT_IDENTIFIER_COLUMNS
        )
        if forbidden:
            raise ValueError(
                "release_columns contain direct identifiers: " + ", ".join(forbidden)
            )
        weight_sum = sum(item.weight for item in self.utility_features)
        if abs(weight_sum - 1.0) > 1e-9:
            raise ValueError("utility feature weights must sum to 1.0")
        if (
            self.synthetic_rows_count_toward_k
            and self.recipient_can_observe_synthetic_provenance
        ):
            raise ValueError(
                "synthetic rows cannot count toward k when recipients can observe "
                "synthetic provenance"
            )
        if self.max_synthetic_rows > 0 and not self.synthetic_rows_count_toward_k:
            raise ValueError(
                "max_synthetic_rows must be 0 unless synthetic_rows_count_toward_k "
                "is explicitly enabled"
            )
        return self


class SensitiveClassMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attribute: str
    distinct_value_count: int = Field(ge=0)
    l_threshold: int | None = Field(default=None, ge=2)
    total_variation_distance: float = Field(ge=0.0, le=1.0)
    t_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    l_satisfied: bool
    t_satisfied: bool


class EquivalenceClassAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quasi_identifier_values: dict[str, str]
    row_count: int = Field(ge=1)
    k_deficit: int = Field(ge=0)
    sensitive_metrics: tuple[SensitiveClassMetric, ...]


class ProjectionAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quasi_identifiers: tuple[str, ...]
    class_count: int = Field(ge=1)
    minimum_class_size: int = Field(ge=1)
    underprotected_class_count: int = Field(ge=0)


class UtilityFeatureAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: UtilityFeatureKind
    discrepancy: float = Field(ge=0.0)
    weight: float = Field(gt=0.0, le=1.0)
    weighted_discrepancy: float = Field(ge=0.0)


class ReleasePredicateAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feasible: bool
    d_util: float = Field(ge=0.0)
    tau_max: float = Field(ge=0.0)
    equivalence_classes: tuple[EquivalenceClassAudit, ...]
    projection_diagnostics: tuple[ProjectionAudit, ...]
    utility_features: tuple[UtilityFeatureAudit, ...]
    violations: tuple[str, ...]


class KPseudonymityAuditManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["released", "no_release"]
    reason: str
    configuration: KPseudonymityReleaseConfig
    original_row_count: int = Field(ge=1)
    release_row_count: int = Field(ge=1)
    synthetic_row_count: int = Field(ge=0)
    synthetic_row_proportion: float = Field(ge=0.0, le=1.0)
    synthetic_row_indices: tuple[int, ...]
    state_evaluations: int = Field(ge=1)
    source_table_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_table_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    initial_predicate: ReleasePredicateAudit
    final_predicate: ReleasePredicateAudit
    real_rows_modified: Literal[False] = False
    reference_distribution: Literal["initial_deidentified_real_table"] = (
        "initial_deidentified_real_table"
    )
    interpretation: Literal[
        "released_table_frequency_property_not_person_level_anonymity"
    ] = "released_table_frequency_property_not_person_level_anonymity"


FORBIDDEN_DIRECT_IDENTIFIER_COLUMNS: frozenset[str] = frozenset(
    {
        "first_name",
        "last_name",
        "patient_first_name",
        "patient_last_name",
        "dob",
        "date_of_birth",
        "birth_date",
        "patient_dob",
        "casenumber",
        "case_number",
        "accession_number",
        "external_id",
        "examiner_first_name",
        "examiner_last_name",
        "file_path",
        "filename",
        "patient_id",
        "mrn",
    }
)


def _require_unique(name: str, values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicate values")


__all__ = [
    "EquivalenceClassAudit",
    "KPseudonymityAuditManifest",
    "KPseudonymityReleaseConfig",
    "ProjectionAudit",
    "ReleasePredicateAudit",
    "RepairCostWeights",
    "SensitiveAttributeConfig",
    "SensitiveClassMetric",
    "UtilityFeatureAudit",
    "UtilityFeatureConfig",
    "UtilityFeatureKind",
]
