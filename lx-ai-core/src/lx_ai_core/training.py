from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lx_ai_core.active_learning import ActiveLearningSelection
from lx_ai_core.contracts import (
    ALLOWED_TASKS,
    JsonDict,
    Modality,
    ModelSpec,
    NestedNumberArray,
    TaskKind,
    _coerce_local_path,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TrainingStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class TrainingArtifactKind(str, Enum):
    CHECKPOINT = "checkpoint"
    MANIFEST = "manifest"
    METADATA = "metadata"
    METRICS = "metrics"


class TrainingSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_index: int = Field(ge=0)
    path: Path | None = None
    array: NestedNumberArray | None = None
    labels: list[float] = Field(min_length=1)
    label_mask: list[int] = Field(default_factory=list)
    group_id: str | None = None
    frame_id: int | None = Field(default=None, ge=0)
    video_id: int | None = Field(default=None, ge=0)
    frame_number: int | None = Field(default=None, ge=0)
    timestamp: float | None = Field(default=None, ge=0.0)
    metadata: JsonDict = Field(default_factory=dict)

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> Path | None:
        if value is None or value == "":
            return None
        if isinstance(value, Path):
            return _coerce_local_path(value)
        if isinstance(value, str):
            return _coerce_local_path(value)
        raise TypeError("path must be a local path string or Path")

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, value: list[float]) -> list[float]:
        return [min(max(float(item), 0.0), 1.0) for item in value]

    @model_validator(mode="after")
    def _validate_sample(self) -> "TrainingSample":
        if self.path is None and self.array is None:
            raise ValueError("training samples require path or array input")
        if not self.label_mask:
            self.label_mask = [1] * len(self.labels)
        if len(self.label_mask) != len(self.labels):
            raise ValueError("label_mask must match labels length")
        invalid_mask_values = [value for value in self.label_mask if value not in {0, 1}]
        if invalid_mask_values:
            raise ValueError("label_mask entries must be 0 or 1")
        return self


class TrainingDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: int | None = Field(default=None, ge=1)
    name: str | None = None
    modality: Modality
    task_kind: TaskKind
    labels: list[str] = Field(min_length=1)
    samples: list[TrainingSample] = Field(min_length=1)
    class_frequencies: list[float] | None = None
    provenance: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_manifest(self) -> "TrainingDatasetManifest":
        allowed = ALLOWED_TASKS[self.modality]
        if self.task_kind not in allowed:
            raise ValueError(
                f"task_kind={self.task_kind.value!r} is not valid for "
                f"modality={self.modality.value!r}"
            )
        label_count = len(self.labels)
        for sample in self.samples:
            if len(sample.labels) != label_count:
                raise ValueError("sample labels must match manifest labels length")
        if self.class_frequencies is not None and (
            len(self.class_frequencies) != label_count
        ):
            raise ValueError("class_frequencies must match manifest labels length")
        return self


class TrainingParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    num_epochs: int = Field(default=5, ge=1)
    batch_size: int = Field(default=32, ge=1)
    learning_rate: float = Field(default=1e-3, gt=0.0)
    validation_split: float = Field(default=0.2, ge=0.0, lt=1.0)
    test_split: float = Field(default=0.1, ge=0.0, lt=1.0)
    random_seed: int = 42
    device: Literal["auto", "cpu", "cuda"] = "auto"
    output_dir: Path | None = None
    run_name: str | None = None
    options: JsonDict = Field(default_factory=dict)

    @field_validator("output_dir", mode="before")
    @classmethod
    def _coerce_output_dir(cls, value: object) -> Path | None:
        if value is None or value == "":
            return None
        if isinstance(value, Path):
            return _coerce_local_path(value)
        if isinstance(value, str):
            return _coerce_local_path(value)
        raise TypeError("output_dir must be a local path string or Path")


class TrainingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_spec: ModelSpec
    dataset: TrainingDatasetManifest
    parameters: TrainingParameters = Field(default_factory=TrainingParameters)
    request_id: str | None = None

    @model_validator(mode="after")
    def _validate_request(self) -> "TrainingRequest":
        if self.model_spec.modality != self.dataset.modality:
            raise ValueError("model_spec modality must match training dataset modality")
        if self.model_spec.task_kind != self.dataset.task_kind:
            raise ValueError(
                "model_spec task_kind must match training dataset task_kind"
            )
        if self.model_spec.labels and self.model_spec.labels != self.dataset.labels:
            raise ValueError("model_spec labels must match training dataset labels")
        return self


class TrainingArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TrainingArtifactKind
    path: Path
    checksum_sha256: str
    bytes: int = Field(ge=0)
    metadata: JsonDict = Field(default_factory=dict)

    @field_validator("path", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> Path:
        if isinstance(value, Path):
            return _coerce_local_path(value)
        if isinstance(value, str):
            return _coerce_local_path(value)
        raise TypeError("path must be a local path string or Path")

    @field_validator("checksum_sha256")
    @classmethod
    def _validate_checksum(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_RE.match(normalized):
            raise ValueError("checksum_sha256 must be 64 lowercase hex characters")
        return normalized


class TrainingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TrainingStatus
    model_spec: ModelSpec
    request_id: str | None = None
    dataset_id: int | None = Field(default=None, ge=1)
    sample_count: int = Field(ge=0)
    artifacts: list[TrainingArtifact] = Field(default_factory=list)
    metrics: JsonDict = Field(default_factory=dict)
    active_learning_selection: ActiveLearningSelection | None = None
    details: str = ""


__all__ = [
    "TrainingArtifact",
    "TrainingArtifactKind",
    "TrainingDatasetManifest",
    "TrainingParameters",
    "TrainingRequest",
    "TrainingResult",
    "TrainingSample",
    "TrainingStatus",
]
