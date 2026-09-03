from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AIFrameFormatStrategy = Literal[
    "preserve_dimensions_black_mask",
    "crop_to_endoscope_roi",
]


def _reject_remote_path(value: str) -> None:
    if "://" in value or value.startswith("//"):
        raise ValueError("training manifest paths must be local paths")


def _coerce_local_path(value: object) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, Path)):
        raise TypeError("path must be a local path string or Path")
    text = str(value).strip()
    _reject_remote_path(text)
    return Path(text)


def _coerce_relative_path(value: object) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    _reject_remote_path(text)
    path = Path(text)
    if path.is_absolute():
        raise ValueError("relative_path must not be absolute")
    if ".." in path.parts:
        raise ValueError("relative_path must not contain parent traversal")
    return path.as_posix()


def _validate_lx_ai_core_training_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from lx_ai_core.training import TrainingDatasetManifest
    except ModuleNotFoundError as exc:
        if exc.name != "lx_ai_core":
            raise
        return payload

    TrainingDatasetManifest.model_validate(payload)
    return payload


class AITrainingLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int = Field(ge=1)
    name: str = Field(min_length=1)
    index: int = Field(ge=0)
    labelset_name: str | None = None
    labelset_version: int | None = None


class AITrainingSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_index: int = Field(ge=0)
    path: Path | None = None
    relative_path: str | None = None
    labels: list[float] = Field(min_length=1)
    label_mask: list[int] = Field(min_length=1)
    group_id: str | None = None
    frame_id: int | None = Field(default=None, ge=1)
    video_id: int | None = Field(default=None, ge=1)
    video_uuid: str | None = None
    frame_number: int | None = Field(default=None, ge=0)
    timestamp: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, value: object) -> Path | None:
        return _coerce_local_path(value)

    @field_validator("relative_path", mode="before")
    @classmethod
    def _validate_relative_path(cls, value: object) -> str | None:
        return _coerce_relative_path(value)

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, value: list[float]) -> list[float]:
        normalized = [float(item) for item in value]
        invalid = [item for item in normalized if item < 0.0 or item > 1.0]
        if invalid:
            raise ValueError("labels must be probabilities in the range [0, 1]")
        return normalized

    @field_validator("label_mask")
    @classmethod
    def _validate_label_mask(cls, value: list[int]) -> list[int]:
        normalized = [int(item) for item in value]
        invalid = [item for item in normalized if item not in {0, 1}]
        if invalid:
            raise ValueError("label_mask entries must be 0 or 1")
        return normalized

    @model_validator(mode="after")
    def _validate_sample(self) -> "AITrainingSample":
        if self.path is None and self.relative_path is None:
            raise ValueError("training samples require path or relative_path")
        if len(self.labels) != len(self.label_mask):
            raise ValueError("label_mask must match labels length")
        return self

    def to_lx_ai_core_dict(self) -> dict[str, Any]:
        metadata = dict(self.metadata)
        if self.relative_path is not None:
            metadata.setdefault("relative_path", self.relative_path)
        if self.video_uuid is not None:
            metadata.setdefault("video_uuid", self.video_uuid)

        path = self.path
        if path is None and self.relative_path is not None:
            path = Path(self.relative_path)

        payload: dict[str, Any] = {
            "sample_index": self.sample_index,
            "labels": self.labels,
            "label_mask": self.label_mask,
            "group_id": self.group_id,
            "frame_id": self.frame_id,
            "video_id": self.video_id,
            "frame_number": self.frame_number,
            "timestamp": self.timestamp,
            "metadata": metadata,
        }
        if path is not None:
            payload["path"] = str(path)
        return payload


class AIFrameFormatManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    check_required: bool = True
    status: Literal["not_checked", "passed", "failed"] = "not_checked"
    checked_frame_count: int = Field(default=0, ge=0)
    expected_image_format: str | None = None
    expected_width: int | None = Field(default=None, ge=1)
    expected_height: int | None = Field(default=None, ge=1)
    expected_mode: str | None = None
    preprocessing_strategy: AIFrameFormatStrategy = "preserve_dimensions_black_mask"
    recommended_model_input_strategy: AIFrameFormatStrategy = "crop_to_endoscope_roi"
    crop_templates_by_video_uuid: dict[str, list[int] | None] = Field(
        default_factory=dict
    )
    notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @field_validator("crop_templates_by_video_uuid")
    @classmethod
    def _validate_crop_templates(
        cls,
        value: dict[str, list[int] | None],
    ) -> dict[str, list[int] | None]:
        normalized: dict[str, list[int] | None] = {}
        for video_uuid, crop_template in value.items():
            if crop_template is None:
                normalized[str(video_uuid)] = None
                continue
            if len(crop_template) != 4:
                raise ValueError("crop templates must be [y1, y2, x1, x2]")
            y1, y2, x1, x2 = [int(item) for item in crop_template]
            if y1 < 0 or x1 < 0 or y2 <= y1 or x2 <= x1:
                raise ValueError("crop templates must define a positive ROI")
            normalized[str(video_uuid)] = [y1, y2, x1, x2]
        return normalized


class AITrainingDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = "1.0"
    dataset_id: int | None = Field(default=None, ge=1)
    name: str | None = None
    description: str | None = None
    modality: Literal["frame"] = "frame"
    task_kind: Literal["multilabel_classification"] = "multilabel_classification"
    labels: list[AITrainingLabel] = Field(min_length=1)
    samples: list[AITrainingSample] = Field(min_length=1)
    frame_format: AIFrameFormatManifest = Field(default_factory=AIFrameFormatManifest)
    class_frequencies: list[float] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("class_frequencies")
    @classmethod
    def _validate_class_frequencies(
        cls,
        value: list[float] | None,
    ) -> list[float] | None:
        if value is None:
            return None
        normalized = [float(item) for item in value]
        invalid = [item for item in normalized if item < 0.0 or item > 1.0]
        if invalid:
            raise ValueError("class_frequencies must be in the range [0, 1]")
        return normalized

    @model_validator(mode="after")
    def _validate_manifest(self) -> "AITrainingDatasetManifest":
        label_count = len(self.labels)
        label_indices = [label.index for label in self.labels]
        if label_indices != list(range(label_count)):
            raise ValueError("labels must be indexed contiguously from 0")
        for sample in self.samples:
            if len(sample.labels) != label_count:
                raise ValueError("sample labels must match manifest labels length")
            if len(sample.label_mask) != label_count:
                raise ValueError("sample label_mask must match manifest labels length")
        if self.class_frequencies is not None and (
            len(self.class_frequencies) != label_count
        ):
            raise ValueError("class_frequencies must match manifest labels length")
        return self

    def to_lx_ai_core_dict(self) -> dict[str, Any]:
        provenance = dict(self.provenance)
        provenance.setdefault(
            "frame_format",
            self.frame_format.model_dump(mode="json"),
        )
        payload = {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "name": self.name,
            "modality": self.modality,
            "task_kind": self.task_kind,
            "labels": [label.name for label in self.labels],
            "samples": [sample.to_lx_ai_core_dict() for sample in self.samples],
            "class_frequencies": self.class_frequencies,
            "provenance": provenance,
        }
        return _validate_lx_ai_core_training_manifest(payload)


AITrainingDatasetManifest.model_rebuild()


__all__ = [
    "AIFrameFormatManifest",
    "AIFrameFormatStrategy",
    "AITrainingDatasetManifest",
    "AITrainingLabel",
    "AITrainingSample",
]
