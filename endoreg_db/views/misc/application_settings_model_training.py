# pyright: reportPrivateUsage=false
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.models.aidataset.aidataset import AIDataSet, AIModelTrainingRun
from endoreg_db.services.jobs.model_training_jobs import (
    MODEL_TRAINING_SERVER_INSTANCE_ID as _MODEL_TRAINING_SERVER_INSTANCE_ID,
)
from endoreg_db.services.jobs.model_training_jobs import (
    _launch_model_training_run,
    _mark_lost_model_training_runs,
    _model_training_run_payload,
)
from endoreg_db.utils.ai.model_training.config import (
    DEFAULT_LABELSET_VERSION_TO_TRAIN,
    TRAINING_ROOT,
)
from endoreg_db.utils.ai.multilabel_dataset_builder import (
    ANNOTATION_SOURCE_SCOPE_ALL,
    normalize_annotation_source_scope,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.misc.application_settings_ai_datasets import (
    _ai_dataset_model_type,
    _ai_dataset_name,
    _ai_dataset_type,
    _application_settings_ai_dataset_entries,
    _application_settings_dataset_entry_data,
)

MODEL_TRAINING_BACKBONE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "gastro_rn50",
        "label": "GastroNet ResNet50",
        "description": "ResNet50 with optional GastroNet checkpoint loading.",
    },
    {
        "value": "resnet50_imagenet",
        "label": "ResNet50 ImageNet",
        "description": "ResNet50 initialized from ImageNet weights.",
    },
    {
        "value": "resnet50_random",
        "label": "ResNet50 Random",
        "description": "ResNet50 with random initialization.",
    },
    {
        "value": "efficientnet_b0_imagenet",
        "label": "EfficientNet-B0 ImageNet",
        "description": "EfficientNet-B0 initialized from ImageNet weights.",
    },
)

MODEL_TRAINING_FEATURE_MODE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "freeze_backbone",
        "label": "Frozen Backbone",
        "description": "Train only the classifier head on top of fixed features.",
    },
    {
        "value": "fine_tune_backbone",
        "label": "Fine-Tune Backbone",
        "description": "Update the full model including the backbone.",
    },
)

MODEL_TRAINING_TARGET_IMAGE_MULTILABEL = "image_multilabel"
MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR = "phi_region_detector"
MODEL_TRAINING_TARGET_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": MODEL_TRAINING_TARGET_IMAGE_MULTILABEL,
        "label": "Image Multilabel Model",
        "description": "Train the current frame-level multilabel classifier.",
    },
    {
        "value": MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR,
        "label": "PHI Region Detector",
        "description": (
            "Train lx-anonymizer's custom ONNX PHI-region detector from a "
            "YOLO detection dataset."
        ),
    },
)
PHI_REGION_DETECTOR_BASE_MODEL_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "yolov8n.pt",
        "label": "YOLOv8 Nano",
        "description": "Fast baseline for small PHI text regions and CPU-friendly tests.",
    },
    {
        "value": "yolov8s.pt",
        "label": "YOLOv8 Small",
        "description": "Higher capacity while still practical on a single workstation GPU.",
    },
    {
        "value": "yolov8m.pt",
        "label": "YOLOv8 Medium",
        "description": "Larger detector for production-quality experiments.",
    },
)

MODEL_TRAINING_SERVER_INSTANCE_ID = _MODEL_TRAINING_SERVER_INSTANCE_ID
launch_model_training_run = _launch_model_training_run
mark_lost_model_training_runs = _mark_lost_model_training_runs
model_training_run_payload = _model_training_run_payload


@dataclass(frozen=True)
class _PhiRegionDetectorTrainingOptions:
    dataset_yaml: str
    output_dir: str
    base_model: str
    run_name: str | None
    epochs: int
    batch_size: int
    input_size: int
    device: str
    workers: int
    patience: int
    export_onnx: bool
    confidence_threshold: float
    nms_threshold: float
    class_ids: str


@dataclass(frozen=True)
class _RawPhiRegionDetectorTrainingOptions:
    epochs: object
    batch_size: object
    input_size: object
    workers: object
    patience: object
    export_onnx: object
    confidence_threshold: object
    nms_threshold: object


@dataclass(frozen=True)
class _ImageTrainingRunRequest:
    dataset: AIDataSet
    backbone_name: str
    feature_mode: str
    backbone_checkpoint: str | None
    epochs: int
    batch_size: int
    labelset_version: int
    device: str
    treat_unlabeled_as_negative: bool
    annotation_source_scope: str


@dataclass(frozen=True)
class _RawImageTrainingRunRequest:
    dataset_id: object
    backbone_name: str
    feature_mode: str
    epochs: object
    batch_size: object
    labelset_version: object
    device: str
    treat_unlabeled_as_negative: object


def _request_payload(data: object) -> dict[str, Any]:
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _normalized_payload_string(
    payload: dict[str, Any],
    field_name: str,
    *,
    default: str,
    empty_value: str = "",
) -> str:
    normalized = str(payload.get(field_name, default) or "").strip()
    return normalized or empty_value


def _coerce_local_training_path(
    value: object,
    *,
    field_name: str,
    required: bool = True,
) -> tuple[str | None, str | None]:
    if value in (None, ""):
        return (None, f"{field_name} is required.") if required else (None, None)
    if not isinstance(value, str):
        return None, f"{field_name} must be a string."
    normalized = value.strip()
    if not normalized:
        return (None, f"{field_name} is required.") if required else (None, None)
    if "://" in normalized or normalized.startswith("//"):
        return None, f"{field_name} must be a local path."
    return str(Path(normalized).expanduser().resolve()), None


def _positive_integer_error(value: object, *, field_name: str) -> str | None:
    if not isinstance(value, int) or value <= 0:
        return f"{field_name} must be a positive integer."
    return None


def _minimum_integer_error(
    value: object,
    *,
    field_name: str,
    minimum: int,
) -> str | None:
    if not isinstance(value, int) or value < minimum:
        return f"{field_name} must be an integer >= {minimum}."
    return None


def _boolean_field_error(value: object, *, field_name: str) -> str | None:
    if not isinstance(value, bool):
        return f"{field_name} must be a boolean."
    return None


def _unit_interval_error(value: object, *, field_name: str) -> str | None:
    if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
        return f"{field_name} must be between 0 and 1."
    return None


def _raw_phi_detector_options(
    payload: dict[str, Any],
) -> _RawPhiRegionDetectorTrainingOptions:
    return _RawPhiRegionDetectorTrainingOptions(
        epochs=payload.get("epochs", 50),
        batch_size=payload.get("batch_size", 16),
        input_size=payload.get("input_size", 640),
        workers=payload.get("workers", 4),
        patience=payload.get("patience", 25),
        export_onnx=payload.get("export_onnx", True),
        confidence_threshold=payload.get("confidence_threshold", 0.35),
        nms_threshold=payload.get("nms_threshold", 0.45),
    )


def _phi_detector_field_errors(
    raw: _RawPhiRegionDetectorTrainingOptions,
    *,
    dataset_yaml_error: str | None,
    output_dir_error: str | None,
    base_model: str,
) -> dict[str, str]:
    possible_errors = {
        "dataset_yaml": dataset_yaml_error,
        "output_dir": output_dir_error,
        "base_model": None if base_model else "base_model is required.",
        "epochs": _positive_integer_error(raw.epochs, field_name="epochs"),
        "batch_size": _positive_integer_error(
            raw.batch_size,
            field_name="batch_size",
        ),
        "input_size": _minimum_integer_error(
            raw.input_size,
            field_name="input_size",
            minimum=32,
        ),
        "workers": _minimum_integer_error(
            raw.workers,
            field_name="workers",
            minimum=0,
        ),
        "patience": _minimum_integer_error(
            raw.patience,
            field_name="patience",
            minimum=0,
        ),
        "export_onnx": _boolean_field_error(
            raw.export_onnx,
            field_name="export_onnx",
        ),
        "confidence_threshold": _unit_interval_error(
            raw.confidence_threshold,
            field_name="confidence_threshold",
        ),
        "nms_threshold": _unit_interval_error(
            raw.nms_threshold,
            field_name="nms_threshold",
        ),
    }
    return {
        field_name: error
        for field_name, error in possible_errors.items()
        if error is not None
    }


def _validated_phi_detector_options(
    payload: dict[str, Any],
    *,
    raw: _RawPhiRegionDetectorTrainingOptions,
    dataset_yaml: str,
    output_dir: str | None,
    base_model: str,
) -> _PhiRegionDetectorTrainingOptions:
    run_name = payload.get("run_name")
    return _PhiRegionDetectorTrainingOptions(
        dataset_yaml=dataset_yaml,
        output_dir=output_dir or str((TRAINING_ROOT / "phi_region_detector").resolve()),
        base_model=base_model,
        run_name=run_name.strip() or None if isinstance(run_name, str) else None,
        epochs=cast(int, raw.epochs),
        batch_size=cast(int, raw.batch_size),
        input_size=cast(int, raw.input_size),
        device=_normalized_payload_string(
            payload,
            "device",
            default="auto",
            empty_value="auto",
        ),
        workers=cast(int, raw.workers),
        patience=cast(int, raw.patience),
        export_onnx=cast(bool, raw.export_onnx),
        confidence_threshold=float(cast(int | float, raw.confidence_threshold)),
        nms_threshold=float(cast(int | float, raw.nms_threshold)),
        class_ids=_normalized_payload_string(payload, "class_ids", default=""),
    )


def _phi_detector_training_options(
    payload: dict[str, Any],
) -> tuple[_PhiRegionDetectorTrainingOptions | None, dict[str, str]]:
    dataset_yaml, dataset_yaml_error = _coerce_local_training_path(
        payload.get("dataset_yaml"), field_name="dataset_yaml"
    )
    output_dir, output_dir_error = _coerce_local_training_path(
        payload.get("output_dir"), field_name="output_dir", required=False
    )
    base_model = _normalized_payload_string(payload, "base_model", default="yolov8n.pt")
    raw = _raw_phi_detector_options(payload)
    errors = _phi_detector_field_errors(
        raw,
        dataset_yaml_error=dataset_yaml_error,
        output_dir_error=output_dir_error,
        base_model=base_model,
    )
    if errors:
        return None, errors
    assert dataset_yaml is not None
    return (
        _validated_phi_detector_options(
            payload,
            raw=raw,
            dataset_yaml=dataset_yaml,
            output_dir=output_dir,
            base_model=base_model,
        ),
        {},
    )


def _create_phi_region_detector_training_run(payload: dict[str, Any]) -> Response:
    options, errors = _phi_detector_training_options(payload)
    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
    assert options is not None
    command_kwargs = {
        "_command_name": "train_phi_region_detector",
        **{
            field_name: getattr(options, field_name)
            for field_name in (
                "dataset_yaml",
                "output_dir",
                "base_model",
                "run_name",
                "epochs",
                "batch_size",
                "input_size",
                "device",
                "workers",
                "patience",
                "export_onnx",
                "confidence_threshold",
                "nms_threshold",
                "class_ids",
            )
        },
    }
    run = AIModelTrainingRun.objects.create(
        dataset=None,
        dataset_name=Path(options.dataset_yaml).name,
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR,
        backbone_name=options.base_model,
        feature_mode="yolo_onnx_detector",
        freeze_backbone=False,
        epochs=options.epochs,
        batch_size=options.batch_size,
        labelset_version=1,
        treat_unlabeled_as_negative=False,
        request_payload={
            **payload,
            "training_target": MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR,
        },
        command_kwargs=command_kwargs,
        status=AIModelTrainingRun.STATUS_QUEUED,
        server_instance_id=_MODEL_TRAINING_SERVER_INSTANCE_ID,
    )
    _launch_model_training_run(run.run_key, command_kwargs=command_kwargs)
    return Response(_model_training_run_payload(run), status=status.HTTP_202_ACCEPTED)


def _raw_image_training_run_request(
    payload: dict[str, Any],
) -> _RawImageTrainingRunRequest:
    return _RawImageTrainingRunRequest(
        dataset_id=payload.get("dataset_id"),
        backbone_name=_normalized_payload_string(
            payload, "backbone_name", default="gastro_rn50"
        ),
        feature_mode=_normalized_payload_string(
            payload, "feature_mode", default="freeze_backbone"
        ),
        epochs=payload.get("epochs", 10),
        batch_size=payload.get("batch_size", 32),
        labelset_version=payload.get(
            "labelset_version", DEFAULT_LABELSET_VERSION_TO_TRAIN
        ),
        device=_normalized_payload_string(
            payload, "device", default="auto", empty_value="auto"
        ),
        treat_unlabeled_as_negative=payload.get("treat_unlabeled_as_negative", True),
    )


def _base_image_training_errors(
    raw: _RawImageTrainingRunRequest,
) -> dict[str, str]:
    errors = _model_training_choice_errors(
        backbone_name=raw.backbone_name,
        feature_mode=raw.feature_mode,
    )
    if not isinstance(raw.dataset_id, int):
        errors["dataset_id"] = "dataset_id must be an integer."
    errors.update(
        _positive_integer_field_errors(
            {
                "epochs": raw.epochs,
                "batch_size": raw.batch_size,
                "labelset_version": raw.labelset_version,
            }
        )
    )
    if not isinstance(raw.treat_unlabeled_as_negative, bool):
        errors["treat_unlabeled_as_negative"] = (
            "treat_unlabeled_as_negative must be a boolean."
        )
    return errors


def _model_training_choice_errors(
    *,
    backbone_name: str,
    feature_mode: str,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if backbone_name not in {
        option["value"] for option in MODEL_TRAINING_BACKBONE_OPTIONS
    }:
        errors["backbone_name"] = "Unsupported backbone_name."
    if feature_mode not in {
        option["value"] for option in MODEL_TRAINING_FEATURE_MODE_OPTIONS
    }:
        errors["feature_mode"] = "Unsupported feature_mode."
    return errors


def _positive_integer_field_errors(
    values: dict[str, object],
) -> dict[str, str]:
    errors: dict[str, str] = {}
    for field_name, value in values.items():
        error = _positive_integer_error(value, field_name=field_name)
        if error is not None:
            errors[field_name] = error
    return errors


def _optional_image_training_values(
    payload: dict[str, Any],
    errors: dict[str, str],
) -> tuple[str | None, str]:
    checkpoint_value = payload.get("backbone_checkpoint")
    checkpoint: str | None = None
    if checkpoint_value not in (None, ""):
        if isinstance(checkpoint_value, str):
            checkpoint = checkpoint_value.strip() or None
        else:
            errors["backbone_checkpoint"] = "backbone_checkpoint must be a string."
    try:
        annotation_scope = normalize_annotation_source_scope(
            cast(str | None, payload.get("annotation_source_scope"))
        )
    except ValueError as exc:
        annotation_scope = ANNOTATION_SOURCE_SCOPE_ALL
        errors["annotation_source_scope"] = str(exc)
    return checkpoint, annotation_scope


def _resolve_image_training_dataset(
    dataset_id: object,
) -> tuple[AIDataSet | None, str | None]:
    if not isinstance(dataset_id, int):
        return None, "dataset_id must be an integer."
    dataset = AIDataSet.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return None, "AIDataSet not found."
    if _ai_dataset_type(dataset) != AIDataSet.DATASET_TYPE_IMAGE:
        return None, "AIDataSet must have dataset_type='image'."
    if _ai_dataset_model_type(dataset) != AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL:
        return (
            None,
            "AIDataSet must have ai_model_type='image_multilabel_classification'.",
        )
    return dataset, None


def _validated_image_training_request(
    raw: _RawImageTrainingRunRequest,
    *,
    dataset: AIDataSet,
    checkpoint: str | None,
    annotation_scope: str,
) -> _ImageTrainingRunRequest:
    assert isinstance(raw.epochs, int)
    assert isinstance(raw.batch_size, int)
    assert isinstance(raw.labelset_version, int)
    assert isinstance(raw.treat_unlabeled_as_negative, bool)
    return _ImageTrainingRunRequest(
        dataset=dataset,
        backbone_name=raw.backbone_name,
        feature_mode=raw.feature_mode,
        backbone_checkpoint=checkpoint,
        epochs=raw.epochs,
        batch_size=raw.batch_size,
        labelset_version=raw.labelset_version,
        device=raw.device,
        treat_unlabeled_as_negative=raw.treat_unlabeled_as_negative,
        annotation_source_scope=annotation_scope,
    )


def _parse_image_training_run_request(
    payload: dict[str, Any],
) -> tuple[_ImageTrainingRunRequest | None, dict[str, str]]:
    raw = _raw_image_training_run_request(payload)
    errors = _base_image_training_errors(raw)
    checkpoint, annotation_scope = _optional_image_training_values(payload, errors)
    if errors:
        return None, errors
    dataset, dataset_error = _resolve_image_training_dataset(raw.dataset_id)
    if dataset_error is not None:
        return None, {"dataset_id": dataset_error}
    assert dataset is not None
    return (
        _validated_image_training_request(
            raw,
            dataset=dataset,
            checkpoint=checkpoint,
            annotation_scope=annotation_scope,
        ),
        {},
    )


def _create_image_training_run(
    payload: dict[str, Any],
    options: _ImageTrainingRunRequest,
) -> Response:
    dataset = options.dataset
    freeze_backbone = options.feature_mode == "freeze_backbone"
    command_kwargs = {
        "_command_name": "train_image_multilabel_model",
        "dataset_id": dataset.pk,
        "backbone_name": options.backbone_name,
        "backbone_checkpoint": options.backbone_checkpoint,
        "epochs": options.epochs,
        "batch_size": options.batch_size,
        "labelset_version": options.labelset_version,
        "device": options.device,
        "freeze_backbone": freeze_backbone,
        "annotation_source_scope": options.annotation_source_scope,
        "treat_unlabeled_as_negative": options.treat_unlabeled_as_negative,
    }
    run = AIModelTrainingRun.objects.create(
        dataset=dataset,
        dataset_name=_ai_dataset_name(dataset),
        dataset_type=_ai_dataset_type(dataset),
        ai_model_type=_ai_dataset_model_type(dataset),
        backbone_name=options.backbone_name,
        feature_mode=options.feature_mode,
        freeze_backbone=freeze_backbone,
        epochs=options.epochs,
        batch_size=options.batch_size,
        labelset_version=options.labelset_version,
        treat_unlabeled_as_negative=options.treat_unlabeled_as_negative,
        backbone_checkpoint=options.backbone_checkpoint,
        request_payload=payload,
        command_kwargs=command_kwargs,
        status=AIModelTrainingRun.STATUS_QUEUED,
        server_instance_id=_MODEL_TRAINING_SERVER_INSTANCE_ID,
    )
    _launch_model_training_run(run.run_key, command_kwargs=command_kwargs)
    return Response(_model_training_run_payload(run), status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_options(request: Request) -> Response:
    return Response(
        {
            "training_targets": list(MODEL_TRAINING_TARGET_OPTIONS),
            "ai_datasets": [
                _application_settings_dataset_entry_data(entry)
                for entry in _application_settings_ai_dataset_entries()
                if entry.dataset_type == AIDataSet.DATASET_TYPE_IMAGE
                and entry.ai_model_type == AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL
            ],
            "backbones": list(MODEL_TRAINING_BACKBONE_OPTIONS),
            "feature_modes": list(MODEL_TRAINING_FEATURE_MODE_OPTIONS),
            "phi_region_detector": {
                "base_models": list(PHI_REGION_DETECTOR_BASE_MODEL_OPTIONS),
                "defaults": {
                    "base_model": "yolov8n.pt",
                    "dataset_yaml": "",
                    "output_dir": str(
                        (TRAINING_ROOT / "phi_region_detector").resolve()
                    ),
                    "run_name": "",
                    "epochs": 50,
                    "batch_size": 16,
                    "input_size": 640,
                    "device": "auto",
                    "workers": 4,
                    "patience": 25,
                    "export_onnx": True,
                    "confidence_threshold": 0.35,
                    "nms_threshold": 0.45,
                    "class_ids": "",
                },
            },
            "defaults": {
                "epochs": 10,
                "batch_size": 32,
                "labelset_version": DEFAULT_LABELSET_VERSION_TO_TRAIN,
                "backbone_name": "gastro_rn50",
                "feature_mode": "freeze_backbone",
                "device": "auto",
                "treat_unlabeled_as_negative": True,
                "backbone_checkpoint": None,
            },
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_runs(request: Request) -> Response:
    _mark_lost_model_training_runs()
    if request.method == "GET":
        runs = AIModelTrainingRun.objects.select_related("dataset").order_by(
            "-created_at", "-id"
        )[:25]
        return Response(
            [_model_training_run_payload(run) for run in runs],
            status=status.HTTP_200_OK,
        )

    payload = _request_payload(request.data)
    training_target = _normalized_payload_string(
        payload,
        "training_target",
        default=MODEL_TRAINING_TARGET_IMAGE_MULTILABEL,
    )
    if training_target == MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR:
        return _create_phi_region_detector_training_run(payload)
    if training_target != MODEL_TRAINING_TARGET_IMAGE_MULTILABEL:
        return Response(
            {"errors": {"training_target": "Unsupported training_target."}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    options, errors = _parse_image_training_run_request(payload)
    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
    assert options is not None
    return _create_image_training_run(payload, options)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_run_detail(
    request: Request, run_id: str
) -> Response:
    _mark_lost_model_training_runs()
    try:
        run_uuid = UUID(str(run_id))
    except (TypeError, ValueError):
        run_uuid = None
    run = (
        AIModelTrainingRun.objects.select_related("dataset")
        .filter(run_id=run_uuid)
        .first()
        if run_uuid is not None
        else None
    )
    if run is None:
        return Response(
            {"detail": "Training run not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(_model_training_run_payload(run), status=status.HTTP_200_OK)


__all__ = [
    "application_settings_model_training_options",
    "application_settings_model_training_runs",
    "application_settings_model_training_run_detail",
]
