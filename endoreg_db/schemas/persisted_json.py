from __future__ import annotations

import math
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal, cast, get_args

from lx_dtypes.models.contracts import CaseResolutionRequest, DocumentType
from lx_dtypes.models.ledger.p_examination.Pydantic import PExamination
from lx_dtypes.serialization import serialize_path
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


def _json_compatible_value(value: Any, *, field_name: str) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} does not allow NaN or infinite floats")
        return value
    if isinstance(value, Path):
        return serialize_path(value)
    if isinstance(value, list):
        sequence = cast(list[Any], value)
        return [
            _json_compatible_value(item, field_name=field_name) for item in sequence
        ]
    if isinstance(value, tuple):
        sequence = cast(tuple[Any, ...], value)
        return [
            _json_compatible_value(item, field_name=field_name) for item in sequence
        ]
    if isinstance(value, dict):
        return _json_compatible_mapping(
            cast(dict[Any, Any], value), field_name=field_name
        )
    raise ValueError(
        f"{field_name} contains unsupported JSON value type: {type(value).__name__}"
    )


def _json_compatible_mapping(
    value: dict[Any, Any], *, field_name: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        payload[key] = _json_compatible_value(item, field_name=field_name)
    return payload


def _validate_model_payload(
    model_cls: type[BaseModel],
    value: Any,
    *,
    field_name: str,
    none_as_empty: bool = False,
) -> dict[str, Any] | None:
    if value is None:
        return {} if none_as_empty else None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    try:
        model = model_cls.model_validate(value)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return model.model_dump(mode="json", exclude_none=True)


def _non_empty_string(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _sha256_hex(value: Any, *, field_name: str) -> str:
    normalized = _non_empty_string(str(value)) if value is not None else None
    if (
        normalized is None
        or len(normalized) != 64
        or any(character not in "0123456789abcdef" for character in normalized.lower())
    ):
        raise ValueError(f"{field_name} must be a 64-character SHA-256 hex digest")
    return normalized.lower()


_EXPLICIT_CASE_RESOLUTION_ACTIONS = frozenset(
    str(action)
    for action in get_args(CaseResolutionRequest.model_fields["action"].annotation)
)
_AUTO_CASE_RESOLUTION_ACTIONS = frozenset({"auto_attach", "auto_create"})
_CASE_RESOLUTION_ACTIONS = (
    _EXPLICIT_CASE_RESOLUTION_ACTIONS | _AUTO_CASE_RESOLUTION_ACTIONS
)


class CaseResolutionMetaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    last_action: str | None = None
    updated_at: str | None = None
    is_explicitly_resolved: bool | None = None
    is_auto_resolved: bool | None = None
    linked_patient_examination_id: int | None = Field(default=None, ge=1)
    linked_patient_id: int | None = Field(default=None, ge=1)
    deferred: bool = False

    @field_validator("last_action", mode="before")
    @classmethod
    def _validate_last_action(cls, value: Any) -> str | None:
        action = _non_empty_string(str(value)) if value is not None else None
        if action is None:
            return None
        if action not in _CASE_RESOLUTION_ACTIONS:
            allowed = ", ".join(sorted(_CASE_RESOLUTION_ACTIONS))
            raise ValueError(f"last_action must be one of: {allowed}")
        return action


class VideoFileMetaPayload(BaseModel):
    """Validated metadata persisted in VideoFile.meta.

    Compatibility note: this field already stores legacy and integration-specific
    keys from transfer payloads and case-resolution workflows. Unknown top-level
    keys are retained only for that compatibility boundary; known safety-critical
    keys are typed here.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    integrity_status: Literal["ok", "warning", "lost"] | None = None
    integrity_error: str | None = None
    integrity_checked_at: str | None = None
    origin: str | None = None
    timestamp: str | None = None
    timestamp_source: str | None = None
    examination_date: str | None = None
    case_resolution: CaseResolutionMetaPayload | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_payload(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return _json_compatible_mapping(
                cast(dict[Any, Any], value), field_name="meta"
            )
        return value

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_json_values(cls, value: Any) -> Any:
        return _json_compatible_value(value, field_name="meta")


class RawPdfMetaPayload(BaseModel):
    """Validated metadata persisted in RawPdfFile.raw_meta.

    Compatibility note: report-reader output, lx-data-models report concepts, and
    older validation flows persist heterogeneous report metadata here. Unknown
    top-level keys are retained for that compatibility boundary; known workflow,
    report-template, and case-resolution keys are typed here.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    document_type: DocumentType | str | None = None
    validation_source: str | None = None
    validated_at: str | None = None
    patient_hash: str | None = None
    examination_hash: str | None = None
    pseudo_patient_id: int | None = Field(default=None, ge=1)
    pseudo_examination_id: int | None = Field(default=None, ge=1)
    center_name: str | None = None
    source: str | None = None
    patient_examination_report_id: int | None = Field(default=None, ge=1)
    template_name: str | None = None
    template_version: str | int | None = None
    template_hash: str | None = None
    version: int | None = Field(default=None, ge=0)
    status: str | None = None
    generated_at: str | None = None
    editor_payload: dict[str, Any] | None = None
    case_resolution: CaseResolutionMetaPayload | None = None
    integrity_status: Literal["ok", "warning", "lost"] | None = None
    integrity_error: str | None = None
    integrity_checked_at: str | None = None

    @field_validator("document_type", mode="before")
    @classmethod
    def _normalize_document_type(cls, value: Any) -> Any:
        document_type = _non_empty_string(str(value)) if value is not None else None
        if document_type is None:
            return None
        try:
            return DocumentType(document_type)
        except ValueError:
            # Compatibility: SAP/table imports and historic metrics can persist
            # local document classes before lx-data-models has a matching enum.
            return document_type

    @field_validator("template_version", mode="before")
    @classmethod
    def _normalize_template_version(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="before")
    @classmethod
    def _coerce_payload(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return _json_compatible_mapping(
                cast(dict[Any, Any], value), field_name="raw_meta"
            )
        return value

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_json_values(cls, value: Any) -> Any:
        return _json_compatible_value(value, field_name="raw_meta")


class TransferSensitiveMetaRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    patient_hash: str
    examination_hash: str

    @field_validator("patient_hash", "examination_hash", mode="before")
    @classmethod
    def _require_pseudonym_hash(cls, value: Any) -> str:
        return _sha256_hex(value, field_name="pseudonym hash")

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_json_values(cls, value: Any) -> Any:
        return _json_compatible_value(value, field_name="resource_rows.sensitive_meta")


class TransferProcessingHistoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    file_hash: str | None = None
    success: bool | None = None


class TransferFrameAnnotationRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    annotation_id: int | str
    annotator: str | None = None
    video_hash: str | None = None
    frame_number: int = Field(ge=0)
    frame_relative_path: str
    frame_timestamp: float | None = Field(default=None, ge=0)
    label_name: str
    value: bool
    float_value: float | None = None
    information_source_name: str

    @field_validator(
        "frame_relative_path",
        "label_name",
        "information_source_name",
        "annotator",
        mode="before",
    )
    @classmethod
    def _require_non_empty_strings(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = _non_empty_string(str(value)) if value is not None else None
        if normalized is None:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_json_values(cls, value: Any) -> Any:
        return _json_compatible_value(
            value, field_name="resource_rows.frame_annotations"
        )


class TransferPatientExaminationReportRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: int | None = Field(default=None, ge=1)
    patient_examination: int | None = Field(default=None, ge=1)
    template_name: str
    template_version: str | int | None = None
    template_hash: str | None = None
    title: str | None = None
    status: Literal["final"] = "final"
    editor_payload: dict[str, Any] | None = None
    rendered_text: str | None = None
    version: int = Field(default=1, ge=1)
    is_active: Literal[True] = True
    finalized_at: str | None = None

    @field_validator(
        "template_name",
        "template_hash",
        "title",
        "rendered_text",
        "finalized_at",
        mode="before",
    )
    @classmethod
    def _normalize_strings(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = _non_empty_string(str(value)) if value is not None else None
        if normalized is None:
            return None
        return normalized

    @field_validator("template_name")
    @classmethod
    def _require_template_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("template_name must not be blank")
        return value

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_json_values(cls, value: Any) -> Any:
        return _json_compatible_value(value, field_name="resource_rows.reports")


def _empty_transfer_frame_annotation_rows() -> list[TransferFrameAnnotationRow]:
    return []


def _empty_transfer_report_rows() -> list[TransferPatientExaminationReportRow]:
    return []


class TransferVideoFileRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    video_hash: str
    processed_video_hash: str | None = None
    fps: float | None = Field(default=None, ge=0)
    duration: float | None = Field(default=None, ge=0)
    frame_count: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    suffix: str | None = None

    @field_validator("video_hash", "processed_video_hash", mode="before")
    @classmethod
    def _normalize_hash(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            return _non_empty_string(value)
        return value


class TransferVideoStateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frames_extracted: bool | None = None
    frames_initialized: bool | None = None
    frame_count: int | None = Field(default=None, ge=0)
    video_meta_extracted: bool | None = None
    text_meta_extracted: bool | None = None
    initial_prediction_completed: bool | None = None
    lvs_created: bool | None = None
    frame_annotations_generated: bool | None = None
    sensitive_meta_processed: bool | None = None
    anonymized: bool | None = None
    anonymization_validated: bool | None = None
    outside_segments_removed: bool | None = None
    processing_error: bool | None = None
    processing_started: bool | None = None
    segment_annotations_created: bool | None = None
    segment_annotations_validated: bool | None = None
    was_created: bool | None = None
    processed_file_sha256: str | None = None

    @field_validator("processed_file_sha256", mode="before")
    @classmethod
    def _validate_processed_file_sha256(cls, value: Any) -> str | None:
        if value in (None, ""):
            return None
        return _sha256_hex(value, field_name="processed_file_sha256")


class TransferVideoResourceRows(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_file: TransferVideoFileRow
    sensitive_meta: TransferSensitiveMetaRow | None = None
    video_state: TransferVideoStateRow | None = None
    processing_history: TransferProcessingHistoryRow | None = None
    frame_annotations: list[TransferFrameAnnotationRow] = Field(
        default_factory=_empty_transfer_frame_annotation_rows
    )
    reports: list[TransferPatientExaminationReportRow] = Field(
        default_factory=_empty_transfer_report_rows
    )


class TransferRawPdfFileRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pdf_hash: str
    anonymized_text: str
    state_report_processing_required: bool | None = None
    state_report_processed: bool | None = None

    @field_validator("pdf_hash", mode="before")
    @classmethod
    def _normalize_hash(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _non_empty_string(value)
        return value

    @field_validator("anonymized_text", mode="before")
    @classmethod
    def _require_anonymized_text(cls, value: Any) -> str:
        normalized = _non_empty_string(str(value)) if value is not None else None
        if normalized is None:
            raise ValueError("anonymized_text must not be blank")
        return normalized


class TransferRawPdfStateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text_meta_extracted: bool | None = None
    initial_prediction_completed: bool | None = None
    sensitive_meta_processed: bool | None = None
    anonymized: bool | None = None
    anonymization_validated: bool | None = None
    processing_started: bool | None = None
    processing_error: bool | None = None
    was_created: bool | None = None
    pdf_meta_extracted: bool | None = None
    processed_file_sha256: str | None = None

    @field_validator("processed_file_sha256", mode="before")
    @classmethod
    def _validate_processed_file_sha256(cls, value: Any) -> str | None:
        if value in (None, ""):
            return None
        return _sha256_hex(value, field_name="processed_file_sha256")


class TransferReportResourceRows(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_pdf_file: TransferRawPdfFileRow
    sensitive_meta: TransferSensitiveMetaRow | None = None
    raw_pdf_state: TransferRawPdfStateRow | None = None
    processing_history: TransferProcessingHistoryRow | None = None
    reports: list[TransferPatientExaminationReportRow] = Field(
        default_factory=_empty_transfer_report_rows
    )


class TransferProcessingSnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_processing_success: bool | None = None


class AIModelTrainingRequestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    training_target: Literal["image_multilabel", "phi_region_detector"] | None = None
    dataset_id: int | None = Field(default=None, ge=1)
    backbone_name: str | None = None
    feature_mode: str | None = None
    epochs: int | None = Field(default=None, ge=1)
    batch_size: int | None = Field(default=None, ge=1)
    labelset_version: int | None = Field(default=None, ge=1)
    device: str | None = None
    annotation_source_scope: Literal["all", "frame_only", "segment_only"] | None = None
    treat_unlabeled_as_negative: bool | None = None
    backbone_checkpoint: str | None = None
    dataset_yaml: str | None = None
    output_dir: str | None = None
    base_model: str | None = None
    run_name: str | None = None
    input_size: int | None = Field(default=None, ge=1)
    workers: int | None = Field(default=None, ge=0)
    patience: int | None = Field(default=None, ge=0)
    export_onnx: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)
    nms_threshold: float | None = Field(default=None, ge=0, le=1)
    class_ids: str | list[int] | None = None


class AIModelTrainingResultPayload(BaseModel):
    """Validated model-training result JSON.

    Compatibility note: training commands can return backend-specific metrics and
    nested lx-ai-core payloads. Unknown top-level keys are retained for trainer
    compatibility; artifact paths are separately normalized into artifact_paths.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    model_path: str | None = None
    manifest_path: str | None = None
    meta_path: str | None = None
    training_result_path: str | None = None
    checkpoint_path: str | None = None
    onnx_path: str | None = None
    training_result: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_payload(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return _json_compatible_mapping(
                cast(dict[Any, Any], value), field_name="result"
            )
        return value

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_json_values(cls, value: Any) -> Any:
        return _json_compatible_value(value, field_name="result")


class AIModelTrainingArtifactPathsPayload(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _validate_artifact_path_mapping(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("artifact_paths must be a JSON object")
        normalized: dict[str, str] = {}
        artifact_paths = cast(dict[Any, Any], value)
        for key, item in artifact_paths.items():
            if item is None:
                return {}
            if not isinstance(key, str):
                raise ValueError("artifact path keys must be strings")
            if not key.endswith("_path"):
                raise ValueError("artifact path keys must end with '_path'")
            if not isinstance(item, (str, Path)):
                raise ValueError("artifact path values must be local path strings")
            serialized = serialize_path(item) if isinstance(item, Path) else item
            if serialized is None:
                return {}
            text = serialized.strip()
            if not text:
                raise ValueError("artifact path values must not be blank")
            if "://" in text or text.startswith("//"):
                raise ValueError("artifact paths must be local paths")
            normalized[key] = text
        return normalized


def validate_transfer_resource_rows(
    value: Any,
    *,
    resource_kind: str | None,
) -> dict[str, Any]:
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict):
        raise ValueError("resource_rows must be a JSON object")
    if resource_kind == "video":
        model_cls: type[BaseModel] = TransferVideoResourceRows
    elif resource_kind == "report":
        model_cls = TransferReportResourceRows
    else:
        raise ValueError(
            f"Unsupported transfer resource_kind for resource_rows: {resource_kind}"
        )
    return (
        _validate_model_payload(
            model_cls,
            value,
            field_name="resource_rows",
            none_as_empty=True,
        )
        or {}
    )


def validate_transfer_processing_snapshot(value: Any) -> dict[str, Any]:
    return (
        _validate_model_payload(
            TransferProcessingSnapshotPayload,
            value,
            field_name="processing_snapshot",
            none_as_empty=True,
        )
        or {}
    )


def validate_video_file_meta_payload(value: Any) -> dict[str, Any] | None:
    return _validate_model_payload(VideoFileMetaPayload, value, field_name="meta")


def validate_raw_pdf_meta_payload(value: Any) -> dict[str, Any] | None:
    return _validate_model_payload(RawPdfMetaPayload, value, field_name="raw_meta")


def validate_dtypes_p_examination_payload(value: Any) -> dict[str, Any]:
    if value is None or value == {}:
        return {}
    if isinstance(value, PExamination):
        return value.model_dump(mode="json", exclude_none=True)
    if not isinstance(value, dict):
        raise ValueError("dtypes_record must be a JSON object")
    try:
        payload = PExamination.model_validate(
            _json_compatible_mapping(
                cast(dict[Any, Any], value),
                field_name="dtypes_record",
            )
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return payload.model_dump(mode="json", exclude_none=True)


def validate_ai_model_training_request_payload(value: Any) -> dict[str, Any]:
    return (
        _validate_model_payload(
            AIModelTrainingRequestPayload,
            value,
            field_name="request_payload",
            none_as_empty=True,
        )
        or {}
    )


def validate_ai_model_training_result_payload(value: Any) -> dict[str, Any] | None:
    return _validate_model_payload(
        AIModelTrainingResultPayload,
        value,
        field_name="result",
    )


def validate_ai_model_training_artifact_paths(value: Any) -> dict[str, str]:
    payload = _validate_model_payload(
        AIModelTrainingArtifactPathsPayload,
        value,
        field_name="artifact_paths",
        none_as_empty=True,
    )
    return dict(payload or {})


__all__ = [
    "AIModelTrainingArtifactPathsPayload",
    "AIModelTrainingRequestPayload",
    "AIModelTrainingResultPayload",
    "RawPdfMetaPayload",
    "TransferFrameAnnotationRow",
    "TransferPatientExaminationReportRow",
    "TransferProcessingSnapshotPayload",
    "TransferReportResourceRows",
    "TransferVideoResourceRows",
    "VideoFileMetaPayload",
    "validate_ai_model_training_artifact_paths",
    "validate_ai_model_training_request_payload",
    "validate_ai_model_training_result_payload",
    "validate_dtypes_p_examination_payload",
    "validate_raw_pdf_meta_payload",
    "validate_transfer_processing_snapshot",
    "validate_transfer_resource_rows",
    "validate_video_file_meta_payload",
]
