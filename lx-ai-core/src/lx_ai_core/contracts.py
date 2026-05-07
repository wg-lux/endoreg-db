from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Modality(str, Enum):
    FRAME = "frame"
    VIDEO = "video"
    SIGNAL = "signal"
    TEXT = "text"
    MATH = "math"


class TaskKind(str, Enum):
    MULTILABEL_CLASSIFICATION = "multilabel_classification"
    SEMANTIC_SEGMENTATION = "semantic_segmentation"
    TEMPORAL_MULTILABEL_SEGMENTATION = "temporal_multilabel_segmentation"
    SIGNAL_CLASSIFICATION = "signal_classification"
    TEXT_CLASSIFICATION = "text_classification"
    MATH_MODEL = "math_model"


class BackendName(str, Enum):
    TORCH = "torch"


ALLOWED_TASKS: dict[Modality, set[TaskKind]] = {
    Modality.FRAME: {
        TaskKind.MULTILABEL_CLASSIFICATION,
        TaskKind.SEMANTIC_SEGMENTATION,
    },
    Modality.VIDEO: {
        TaskKind.TEMPORAL_MULTILABEL_SEGMENTATION,
    },
    Modality.SIGNAL: {
        TaskKind.SIGNAL_CLASSIFICATION,
    },
    Modality.TEXT: {
        TaskKind.TEXT_CLASSIFICATION,
    },
    Modality.MATH: {
        TaskKind.MATH_MODEL,
    },
}


def _coerce_local_path(value: str | Path) -> Path:
    raw = str(value)
    if "://" in raw or raw.startswith("//"):
        raise ValueError("remote paths and URLs are not accepted by lx-ai-core")
    return Path(value).expanduser()


JsonDict = dict[str, Any]
NestedNumberArray = list[Any]


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    task_kind: TaskKind
    modality: Modality
    backend: BackendName = BackendName.TORCH
    version: str | None = None
    artifact_path: Path | None = None
    entrypoint: str | None = Field(
        default=None,
        description="Import target in module:attribute form for constructing a model.",
    )
    labels: list[str] = Field(default_factory=list)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    dtype: Literal["float32", "float16", "bfloat16"] = "float32"
    parameters: JsonDict = Field(default_factory=dict)

    @field_validator("artifact_path", mode="before")
    @classmethod
    def _coerce_artifact_path(cls, value: object) -> Path | None:
        if value is None or value == "":
            return None
        if isinstance(value, Path):
            return _coerce_local_path(value)
        if isinstance(value, str):
            return _coerce_local_path(value)
        raise TypeError("artifact_path must be a local path string or Path")

    @model_validator(mode="after")
    def _validate_task_modality_pair(self) -> "ModelSpec":
        allowed = ALLOWED_TASKS[self.modality]
        if self.task_kind not in allowed:
            raise ValueError(
                f"task_kind={self.task_kind.value!r} is not valid for "
                f"modality={self.modality.value!r}"
            )
        return self

    @property
    def cache_key(self) -> str:
        artifact = str(self.artifact_path.resolve()) if self.artifact_path else ""
        labels = ",".join(self.labels)
        return "|".join(
            [
                self.backend.value,
                self.modality.value,
                self.task_kind.value,
                self.name,
                self.version or "",
                artifact,
                self.entrypoint or "",
                labels,
                self.dtype,
            ]
        )


class InferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path | None = None
    paths: list[Path] = Field(default_factory=list)
    array: NestedNumberArray | None = None
    frame_scores: list[list[float]] | None = None
    text: str | None = None
    expression: str | None = None
    vector: list[float] | None = None
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

    @field_validator("paths", mode="before")
    @classmethod
    def _coerce_paths(cls, value: object) -> list[Path]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("paths must be a list of local path strings")
        out: list[Path] = []
        for item in value:
            if isinstance(item, Path):
                out.append(_coerce_local_path(item))
            elif isinstance(item, str):
                out.append(_coerce_local_path(item))
            else:
                raise TypeError("paths entries must be local path strings or Paths")
        return out

    @model_validator(mode="after")
    def _require_some_input(self) -> "InferenceInput":
        if not any(
            [
                self.path is not None,
                bool(self.paths),
                self.array is not None,
                self.frame_scores is not None,
                self.text is not None,
                self.expression is not None,
                self.vector is not None,
            ]
        ):
            raise ValueError("at least one input field must be provided")
        return self


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_spec: ModelSpec
    inputs: InferenceInput
    options: JsonDict = Field(default_factory=dict)
    request_id: str | None = None

    @model_validator(mode="after")
    def _validate_input_for_modality(self) -> "InferenceRequest":
        modality = self.model_spec.modality
        task = self.model_spec.task_kind
        inputs = self.inputs

        if modality == Modality.FRAME and not (
            inputs.path is not None or inputs.array is not None
        ):
            raise ValueError("frame requests require path or array input")

        if modality == Modality.VIDEO and not (
            inputs.paths or inputs.frame_scores is not None or inputs.array is not None
        ):
            raise ValueError("video requests require paths, frame_scores, or array input")

        if modality == Modality.SIGNAL and inputs.array is None:
            raise ValueError("signal requests require array input")

        if modality == Modality.TEXT and not inputs.text:
            raise ValueError("text requests require text input")

        if modality == Modality.MATH and not (
            inputs.expression or inputs.vector is not None or inputs.array is not None
        ):
            raise ValueError("math requests require expression, vector, or array input")

        if task == TaskKind.TEMPORAL_MULTILABEL_SEGMENTATION and inputs.path is not None:
            raise ValueError("video temporal requests use paths, frame_scores, or array")

        return self


class Score(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    score: float = Field(..., ge=0.0, le=1.0)


class ScoreVector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int | None = None
    scores: list[Score]


class TemporalSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    start_frame: int = Field(..., ge=0)
    end_frame: int = Field(..., ge=0)
    score: float = Field(..., ge=0.0, le=1.0)
    peak_score: float | None = Field(default=None, ge=0.0, le=1.0)
    frame_count: int | None = Field(default=None, ge=1)
    source: str = "model"

    @model_validator(mode="after")
    def _validate_range(self) -> "TemporalSegment":
        if self.end_frame < self.start_frame:
            raise ValueError("end_frame must be greater than or equal to start_frame")
        return self


class MaskArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: list[int] = Field(..., min_length=2)
    encoding: Literal["rle"] = "rle"
    counts: list[int] | None = None
    artifact_path: Path | None = None
    class_label: str | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("artifact_path", mode="before")
    @classmethod
    def _coerce_artifact_path(cls, value: object) -> Path | None:
        if value is None or value == "":
            return None
        if isinstance(value, Path):
            return _coerce_local_path(value)
        if isinstance(value, str):
            return _coerce_local_path(value)
        raise TypeError("artifact_path must be a local path string or Path")

    @model_validator(mode="after")
    def _require_payload_or_path(self) -> "MaskArtifact":
        if self.counts is None and self.artifact_path is None:
            raise ValueError("mask artifact requires counts or artifact_path")
        return self


class RunMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: float = Field(..., ge=0.0)
    input_count: int = Field(default=1, ge=0)
    batch_size: int | None = Field(default=None, ge=1)
    backend: str
    device: str


class InferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_spec: ModelSpec
    backend: str
    device: str
    duration_ms: float = Field(..., ge=0.0)
    provenance: JsonDict = Field(default_factory=dict)
    score_vectors: list[ScoreVector] = Field(default_factory=list)
    temporal_segments: list[TemporalSegment] = Field(default_factory=list)
    masks: list[MaskArtifact] = Field(default_factory=list)
    raw_output: JsonDict | None = None
    metrics: RunMetrics | None = None
