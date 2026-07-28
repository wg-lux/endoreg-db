# pyright: reportPrivateUsage=false, reportUnusedFunction=false
from __future__ import annotations

import json
import threading
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as datetime_timezone
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from django.db import models
from lx_dtypes.models.contracts.application_settings import (
    ApplicationSettingsBackupSourcePayload,
    ApplicationSettingsBackupStatusPayload,
    ApplicationSettingsPayload,
)
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.aidataset.aidataset import AIDataSet, AIModelTrainingRun
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.hub import deployment_profile_payload
from endoreg_db.services.jobs.model_training_jobs import (
    MODEL_TRAINING_SERVER_INSTANCE_ID as _MODEL_TRAINING_SERVER_INSTANCE_ID,
)
from endoreg_db.services.jobs.model_training_jobs import (
    _launch_model_training_run,
    _mark_lost_model_training_runs,
    _model_training_run_payload,
)
from endoreg_db.services.video_dimension_backfill import (
    VideoDimensionBackfillResult,
    backfill_anonymized_video_dimensions,
)
from endoreg_db.utils.ai.model_training.config import (
    DEFAULT_LABELSET_VERSION_TO_TRAIN,
    TRAINING_ROOT,
)
from endoreg_db.utils.ai.multilabel_dataset_builder import (
    ANNOTATION_SOURCE_SCOPE_ALL,
    normalize_annotation_source_scope,
)
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
)
from endoreg_db.utils.paths import PROTECTED_DATA_ROOT, STORAGE_DIR
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.set_default_center import (
    get_application_defaults,
    get_application_settings,
    update_application_defaults,
)
from endoreg_db.views.misc.application_settings_ai_datasets import (
    _ai_dataset_model_type,
    _ai_dataset_name,
    _ai_dataset_type,
    _application_settings_ai_dataset_entries,
    _application_settings_dataset_entry_data,
    _parse_optional_integer_param,
    application_settings_ai_dataset_attachments,
    application_settings_ai_dataset_export,
    application_settings_ai_dataset_export_download,
    application_settings_ai_dataset_frame_bucket_distribution,
    application_settings_ai_dataset_training_manifest,
    application_settings_ai_datasets_dropdown,
)
from endoreg_db.views.misc.application_settings_network_nodes import (
    application_settings_network_node_detail,
    application_settings_network_node_roles_dropdown,
    application_settings_network_nodes,
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

_VIDEO_DIMENSION_BACKFILL_RUNS: dict[str, dict[str, Any]] = {}
_VIDEO_DIMENSION_BACKFILL_RUNS_LOCK = threading.Lock()

MODEL_TRAINING_SERVER_INSTANCE_ID = _MODEL_TRAINING_SERVER_INSTANCE_ID

launch_model_training_run = _launch_model_training_run
mark_lost_model_training_runs = _mark_lost_model_training_runs
model_training_run_payload = _model_training_run_payload


class _RequestUserWithUsername(Protocol):
    is_authenticated: bool
    username: str


def _request_user_with_username(request: Request) -> _RequestUserWithUsername:
    return cast(_RequestUserWithUsername, request.user)


def _center_field(value: Center | None) -> Center | None:
    return value


def _model_datetime(value: datetime | None) -> datetime | None:
    return value


def _required_backup_sources() -> list[Path]:
    sources: list[Path] = []
    for path in (PROTECTED_DATA_ROOT, STORAGE_DIR):
        if path not in sources:
            sources.append(path)
    return sources


def _count_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file())


def _backup_source_label(index: int, path: Path) -> str:
    if path == PROTECTED_DATA_ROOT:
        return "protected_root"
    if path == STORAGE_DIR:
        return "storage"
    if index == 0:
        return "storage"
    if index == 1:
        return "io"
    return f"source_{index + 1}"


def _backup_status_payload() -> ApplicationSettingsBackupStatusPayload:
    required_sources = [path.resolve() for path in _required_backup_sources()]
    missing_paths = [str(path) for path in required_sources if not path.exists()]
    source_roots = [
        ApplicationSettingsBackupSourcePayload(
            label=_backup_source_label(index, path),
            path=str(path),
            exists=path.exists(),
            file_count=_count_files(path) if path.exists() else 0,
        )
        for index, path in enumerate(required_sources)
    ]
    return ApplicationSettingsBackupStatusPayload(
        ready=len(missing_paths) == 0,
        missing_paths=missing_paths,
        required_path_count=len(required_sources),
        available_path_count=len(required_sources) - len(missing_paths),
        source_roots=source_roots,
    )


def _copy_backup_source_tree(source_root: Path, destination_root: Path) -> int:
    ensure_directory(destination_root)
    copied_count = 0

    for source_path in source_root.rglob("*"):
        relative_path = source_path.relative_to(source_root)
        destination_path = destination_root / relative_path
        if source_path.is_dir():
            ensure_directory(destination_path)
            continue
        if not source_path.is_file():
            continue

        atomic_copy_file(source=source_path, destination=destination_path)
        copied_count += 1

    return copied_count


def _settings_payload(request: Request) -> ApplicationSettingsPayload:
    settings_obj = get_application_settings()
    snapshot = get_application_defaults()
    annotator_name = snapshot.annotator_name
    if not annotator_name and _request_user_with_username(request).is_authenticated:
        annotator_name = _request_user_with_username(request).username or ""
    center = _center_field(cast(Center | None, getattr(settings_obj, "center", None)))
    updated_at = _model_datetime(
        cast(datetime | None, getattr(settings_obj, "updated_at", None))
    )
    return ApplicationSettingsPayload(
        id=model_pk(settings_obj),
        center_id=snapshot.center_id,
        center_name=snapshot.center_name or "",
        processor_id=snapshot.processor_id,
        processor_name=snapshot.processor_name or "",
        annotator_name=annotator_name,
        report_template_name=snapshot.report_template_name,
        ai_dataset_id=snapshot.ai_dataset_id,
        ai_dataset_name=snapshot.ai_dataset_name or "",
        ai_dataset_type=snapshot.ai_dataset_type or "",
        center_key=(
            cast(str, getattr(center, "center_key", "")) if center is not None else None
        ),
        updated_at=updated_at.isoformat() if updated_at is not None else None,
        deployment_profile=deployment_profile_payload(),
        backup_status=_backup_status_payload(),
    )


def _request_payload(data: object) -> dict[str, Any]:
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _application_settings_payload_data(
    payload: ApplicationSettingsPayload,
) -> dict[str, Any]:
    return payload.model_dump(mode="python")


def _utcnow_iso() -> str:
    return datetime.now(datetime_timezone.utc).isoformat().replace("+00:00", "Z")


def _store_video_dimension_backfill_run(
    run_key: str,
    **updates: object,
) -> dict[str, Any]:
    with _VIDEO_DIMENSION_BACKFILL_RUNS_LOCK:
        current = _VIDEO_DIMENSION_BACKFILL_RUNS.setdefault(run_key, {})
        current.update(updates)
        return dict(current)


def _get_video_dimension_backfill_run(run_id: str) -> dict[str, Any] | None:
    with _VIDEO_DIMENSION_BACKFILL_RUNS_LOCK:
        run = _VIDEO_DIMENSION_BACKFILL_RUNS.get(run_id)
        return dict(run) if run is not None else None


def store_video_dimension_backfill_run(
    run_key: str,
    **updates: object,
) -> dict[str, Any]:
    return _store_video_dimension_backfill_run(run_key, **updates)


def launch_video_dimension_backfill_run(
    run_id: str,
    *,
    command_kwargs: dict[str, Any],
) -> None:
    _launch_video_dimension_backfill_run(run_id, command_kwargs=command_kwargs)


def required_backup_sources() -> list[Path]:
    return _required_backup_sources()


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _coerce_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _coerce_local_training_path(
    value: object,
    *,
    field_name: str,
    required: bool = True,
) -> tuple[str | None, str | None]:
    if value in (None, ""):
        if required:
            return None, f"{field_name} is required."
        return None, None
    if not isinstance(value, str):
        return None, f"{field_name} must be a string."
    normalized = value.strip()
    if not normalized:
        if required:
            return None, f"{field_name} is required."
        return None, None
    if "://" in normalized or normalized.startswith("//"):
        return None, f"{field_name} must be a local path."
    return str(Path(normalized).expanduser().resolve()), None


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


def _optional_nonblank_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _required_string_error(value: str) -> str | None:
    if not value:
        return "base_model is required."
    return None


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


def _present_field_errors(
    field_errors: dict[str, str | None],
) -> dict[str, str]:
    return {
        field_name: error
        for field_name, error in field_errors.items()
        if error is not None
    }


def _phi_detector_output_dir(output_dir: str | None) -> str:
    if output_dir is not None:
        return output_dir
    return str((TRAINING_ROOT / "phi_region_detector").resolve())


def _phi_detector_training_options(
    payload: dict[str, Any],
) -> tuple[_PhiRegionDetectorTrainingOptions | None, dict[str, str]]:
    dataset_yaml, dataset_yaml_error = _coerce_local_training_path(
        payload.get("dataset_yaml"),
        field_name="dataset_yaml",
    )
    output_dir, output_dir_error = _coerce_local_training_path(
        payload.get("output_dir"),
        field_name="output_dir",
        required=False,
    )
    resolved_output_dir = _phi_detector_output_dir(output_dir)
    base_model = _normalized_payload_string(
        payload,
        "base_model",
        default="yolov8n.pt",
    )
    epochs = payload.get("epochs", 50)
    batch_size = payload.get("batch_size", 16)
    input_size = payload.get("input_size", 640)
    workers = payload.get("workers", 4)
    patience = payload.get("patience", 25)
    export_onnx = payload.get("export_onnx", True)
    confidence_threshold = payload.get("confidence_threshold", 0.35)
    nms_threshold = payload.get("nms_threshold", 0.45)
    errors = _present_field_errors(
        {
            "dataset_yaml": dataset_yaml_error,
            "output_dir": output_dir_error,
            "base_model": _required_string_error(base_model),
            "epochs": _positive_integer_error(epochs, field_name="epochs"),
            "batch_size": _positive_integer_error(
                batch_size,
                field_name="batch_size",
            ),
            "input_size": _minimum_integer_error(
                input_size,
                field_name="input_size",
                minimum=32,
            ),
            "workers": _minimum_integer_error(
                workers,
                field_name="workers",
                minimum=0,
            ),
            "patience": _minimum_integer_error(
                patience,
                field_name="patience",
                minimum=0,
            ),
            "export_onnx": _boolean_field_error(
                export_onnx,
                field_name="export_onnx",
            ),
            "confidence_threshold": _unit_interval_error(
                confidence_threshold,
                field_name="confidence_threshold",
            ),
            "nms_threshold": _unit_interval_error(
                nms_threshold,
                field_name="nms_threshold",
            ),
        }
    )
    if errors:
        return None, errors
    assert dataset_yaml is not None
    return (
        _PhiRegionDetectorTrainingOptions(
            dataset_yaml=dataset_yaml,
            output_dir=resolved_output_dir,
            base_model=base_model,
            run_name=_optional_nonblank_string(payload.get("run_name")),
            epochs=cast(int, epochs),
            batch_size=cast(int, batch_size),
            input_size=cast(int, input_size),
            device=_normalized_payload_string(
                payload,
                "device",
                default="auto",
                empty_value="auto",
            ),
            workers=cast(int, workers),
            patience=cast(int, patience),
            export_onnx=cast(bool, export_onnx),
            confidence_threshold=float(cast(int | float, confidence_threshold)),
            nms_threshold=float(cast(int | float, nms_threshold)),
            class_ids=_normalized_payload_string(
                payload,
                "class_ids",
                default="",
            ),
        ),
        {},
    )


def _phi_detector_training_command_kwargs(
    options: _PhiRegionDetectorTrainingOptions,
) -> dict[str, Any]:
    return {
        "_command_name": "train_phi_region_detector",
        "dataset_yaml": options.dataset_yaml,
        "output_dir": options.output_dir,
        "base_model": options.base_model,
        "run_name": options.run_name,
        "epochs": options.epochs,
        "batch_size": options.batch_size,
        "input_size": options.input_size,
        "device": options.device,
        "workers": options.workers,
        "patience": options.patience,
        "export_onnx": options.export_onnx,
        "confidence_threshold": options.confidence_threshold,
        "nms_threshold": options.nms_threshold,
        "class_ids": options.class_ids,
    }


def _create_phi_detector_training_model(
    *,
    payload: dict[str, Any],
    options: _PhiRegionDetectorTrainingOptions,
    command_kwargs: dict[str, Any],
) -> AIModelTrainingRun:
    return AIModelTrainingRun.objects.create(
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


def _create_phi_region_detector_training_run(payload: dict[str, Any]) -> Response:
    options, errors = _phi_detector_training_options(payload)
    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
    assert options is not None
    command_kwargs = _phi_detector_training_command_kwargs(options)
    run = _create_phi_detector_training_model(
        payload=payload,
        options=options,
        command_kwargs=command_kwargs,
    )
    _launch_model_training_run(run.run_key, command_kwargs=command_kwargs)
    return Response(_model_training_run_payload(run), status=status.HTTP_202_ACCEPTED)


def _video_dimension_backfill_item_payload(
    result: VideoDimensionBackfillResult,
) -> dict[str, Any]:
    return {
        "video_id": result.video_id,
        "status": result.status,
        "source_dimensions": list(result.source_dimensions),
        "processed_dimensions": list(result.processed_dimensions),
        "repaired": result.repaired,
        "detail": result.detail,
    }


def _video_dimension_backfill_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "dry_run": run["dry_run"],
        "limit": run.get("limit"),
        "created_at": run["created_at"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "result": run.get("result"),
        "error": run.get("error"),
        "stdout": run.get("stdout", ""),
    }


def _execute_video_dimension_backfill_run(
    run_id: str,
    *,
    command_kwargs: dict[str, Any],
) -> None:
    _store_video_dimension_backfill_run(
        run_id,
        status="running",
        started_at=_utcnow_iso(),
    )
    try:
        results = backfill_anonymized_video_dimensions(**command_kwargs)
        items = [_video_dimension_backfill_item_payload(result) for result in results]
        summary = Counter(item["status"] for item in items)
        _store_video_dimension_backfill_run(
            run_id,
            status="completed",
            finished_at=_utcnow_iso(),
            result={
                "count": len(items),
                "summary": dict(summary),
                "items": items,
            },
            error=None,
            stdout="",
        )
    except Exception as exc:
        _store_video_dimension_backfill_run(
            run_id,
            status="failed",
            finished_at=_utcnow_iso(),
            result=None,
            error=str(exc),
            stdout=traceback.format_exc(),
        )


def _launch_video_dimension_backfill_run(
    run_id: str,
    *,
    command_kwargs: dict[str, Any],
) -> None:
    thread = threading.Thread(
        target=_execute_video_dimension_backfill_run,
        kwargs={"run_id": run_id, "command_kwargs": command_kwargs},
        daemon=True,
    )
    thread.start()


def _normalize_optional_setting(
    data: dict[str, Any],
    field_name: str,
) -> object:
    value = data.get(field_name)
    return "" if field_name in data and value is None else value


def _named_setting_exists(
    model: type[models.Model],
    value: object,
) -> bool:
    lookup = {"pk": value} if isinstance(value, int) else {"name": value}
    return model.objects.filter(**lookup).exists()


def _resolve_optional_named_setting(
    data: dict[str, Any],
    *,
    id_field: str,
    name_field: str,
    model: type[models.Model],
    error_field: str,
    not_found_message: str,
    errors: dict[str, str],
) -> object:
    value = data.get(id_field, data.get(name_field))
    if not data.keys() & {id_field, name_field}:
        return None
    if value in ("", 0):
        return None
    if value is None:
        return value
    if not _named_setting_exists(model, value):
        errors[error_field] = not_found_message
    return value


def _validate_optional_string_settings(
    values: dict[str, object],
    errors: dict[str, str],
) -> None:
    for field_name, value in values.items():
        if value is not None and not isinstance(value, str):
            errors[field_name] = f"{field_name} must be a string."


def _validate_ai_dataset_type(value: object, errors: dict[str, str]) -> None:
    if value is None or not isinstance(value, str):
        return
    if value not in {"", AIDataSet.DATASET_TYPE_IMAGE, AIDataSet.DATASET_TYPE_VIDEO}:
        errors["ai_dataset_type"] = "ai_dataset_type must be one of: image, video."


def _resolve_settings_ai_dataset(
    data: dict[str, Any],
    *,
    errors: dict[str, str],
) -> tuple[AIDataSet | None, object, object, Response | None]:
    dataset_name = _normalize_optional_setting(data, "ai_dataset_name")
    dataset_type = _normalize_optional_setting(data, "ai_dataset_type")
    if "ai_dataset_id" not in data:
        return None, dataset_name, dataset_type, None

    dataset_id, id_error = _parse_optional_integer_param(
        data.get("ai_dataset_id"),
        field_name="ai_dataset_id",
    )
    if id_error is not None:
        return None, dataset_name, dataset_type, id_error
    if dataset_id is None:
        return None, dataset_name, dataset_type, None

    dataset = AIDataSet.objects.filter(pk=dataset_id).first()
    if dataset is None:
        errors["ai_dataset_id"] = "AIDataSet not found."
        return None, dataset_name, dataset_type, None
    return _settings_dataset_values(data, dataset, dataset_name, dataset_type)


def _settings_dataset_values(
    data: dict[str, Any],
    dataset: AIDataSet,
    dataset_name: object,
    dataset_type: object,
) -> tuple[AIDataSet, object, object, None]:
    if "ai_dataset_name" not in data:
        dataset_name = _ai_dataset_name(dataset)
    if "ai_dataset_type" not in data:
        dataset_type = _ai_dataset_type(dataset)
    return dataset, dataset_name, dataset_type, None


def _patch_application_settings(request: Request) -> Response:
    data = _request_payload(request.data)
    errors: dict[str, str] = {}
    center = _resolve_optional_named_setting(
        data,
        id_field="center_id",
        name_field="center_name",
        model=Center,
        error_field="center",
        not_found_message="Center not found.",
        errors=errors,
    )
    processor = _resolve_optional_named_setting(
        data,
        id_field="processor_id",
        name_field="processor_name",
        model=EndoscopyProcessor,
        error_field="processor",
        not_found_message="Processor not found.",
        errors=errors,
    )
    annotator_name = _normalize_optional_setting(data, "annotator_name")
    report_template_name = _normalize_optional_setting(data, "report_template_name")
    ai_dataset, ai_dataset_name, ai_dataset_type, dataset_error = (
        _resolve_settings_ai_dataset(data, errors=errors)
    )
    if dataset_error is not None:
        return dataset_error

    string_settings = {
        "annotator_name": annotator_name,
        "report_template_name": report_template_name,
        "ai_dataset_name": ai_dataset_name,
        "ai_dataset_type": ai_dataset_type,
    }
    _validate_optional_string_settings(string_settings, errors)
    _validate_ai_dataset_type(ai_dataset_type, errors)
    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    update_kwargs: dict[str, Any] = {
        "center": center,
        "processor": processor,
        **string_settings,
    }
    if "ai_dataset_id" in data:
        update_kwargs["ai_dataset"] = ai_dataset
    update_application_defaults(**update_kwargs)
    return Response(
        _application_settings_payload_data(_settings_payload(request)),
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "PATCH"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_detail(request: Request) -> Response:
    if request.method == "GET":
        return Response(
            _application_settings_payload_data(_settings_payload(request)),
            status=status.HTTP_200_OK,
        )
    return _patch_application_settings(request)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_centers_dropdown(request: Request) -> Response:
    centers = Center.objects.order_by("name").values("id", "name", "center_key")
    return Response(list(centers), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_processors_dropdown(request: Request) -> Response:
    processors = EndoscopyProcessor.objects.order_by("name").values("id", "name")
    return Response(list(processors), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_annotators_dropdown(request: Request) -> Response:
    values = list(
        ImageClassificationAnnotation.objects.exclude(annotator__isnull=True)
        .exclude(annotator__exact="")
        .order_by("annotator")
        .values_list("annotator", flat=True)
        .distinct()
    )
    current_value = cast(str, getattr(get_application_settings(), "annotator_name", ""))
    if current_value and current_value not in values:
        values.insert(0, current_value)
    return Response(
        [{"value": value, "label": value} for value in values],
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_report_templates_dropdown(request: Request) -> Response:
    values = list(
        PatientExaminationReport.objects.exclude(template_name__exact="")
        .order_by("template_name")
        .values_list("template_name", flat=True)
        .distinct()
    )
    current_value = cast(
        str,
        getattr(get_application_settings(), "report_template_name", ""),
    )
    if current_value and current_value not in values:
        values.insert(0, current_value)
    return Response(
        [{"value": value, "label": value} for value in values],
        status=status.HTTP_200_OK,
    )


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


def _normalized_payload_string(
    payload: dict[str, Any],
    field_name: str,
    *,
    default: str,
    empty_value: str = "",
) -> str:
    normalized = str(payload.get(field_name, default) or "").strip()
    return normalized or empty_value


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
            "labelset_version",
            DEFAULT_LABELSET_VERSION_TO_TRAIN,
        ),
        device=_normalized_payload_string(
            payload,
            "device",
            default="auto",
            empty_value="auto",
        ),
        treat_unlabeled_as_negative=payload.get(
            "treat_unlabeled_as_negative",
            True,
        ),
    )


def _positive_integer_field_errors(
    values: dict[str, object],
) -> dict[str, str]:
    errors: dict[str, str] = {}
    for field_name, value in values.items():
        if not isinstance(value, int) or value <= 0:
            errors[field_name] = f"{field_name} must be a positive integer."
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


def _normalize_backbone_checkpoint(
    value: object,
) -> tuple[str | None, str | None]:
    if value in (None, ""):
        return None, None
    if not isinstance(value, str):
        return None, "backbone_checkpoint must be a string."
    return value.strip() or None, None


def _normalize_training_annotation_source_scope(
    value: object,
) -> tuple[str, str | None]:
    try:
        return normalize_annotation_source_scope(cast(str | None, value)), None
    except ValueError as exc:
        return ANNOTATION_SOURCE_SCOPE_ALL, str(exc)


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


def _optional_image_training_values(
    payload: dict[str, Any],
    errors: dict[str, str],
) -> tuple[str | None, str]:
    checkpoint, checkpoint_error = _normalize_backbone_checkpoint(
        payload.get("backbone_checkpoint")
    )
    if checkpoint_error is not None:
        errors["backbone_checkpoint"] = checkpoint_error
    annotation_scope, scope_error = _normalize_training_annotation_source_scope(
        payload.get("annotation_source_scope")
    )
    if scope_error is not None:
        errors["annotation_source_scope"] = scope_error
    return checkpoint, annotation_scope


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

    dataset: AIDataSet | None = None
    if not errors:
        dataset, dataset_error = _resolve_image_training_dataset(raw.dataset_id)
        if dataset_error is not None:
            errors["dataset_id"] = dataset_error
    if errors:
        return None, errors

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


def _model_training_runs_payload() -> Response:
    runs = AIModelTrainingRun.objects.select_related("dataset").order_by(
        "-created_at",
        "-id",
    )[:25]
    return Response(
        [_model_training_run_payload(run) for run in runs],
        status=status.HTTP_200_OK,
    )


def _post_model_training_run(request: Request) -> Response:
    payload: dict[str, Any] = _request_payload(request.data)
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


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_runs(request: Request) -> Response:
    _mark_lost_model_training_runs()
    if request.method == "GET":
        return _model_training_runs_payload()
    return _post_model_training_run(request)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_run_detail(
    request: Request, run_id: str
) -> Response:
    _mark_lost_model_training_runs()
    run_uuid = _coerce_uuid(run_id)
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


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_video_dimension_backfill_runs(request: Request) -> Response:
    payload: dict[str, Any] = _request_payload(request.data)
    dry_run = payload.get("dry_run", False)
    limit = payload.get("limit")

    errors: dict[str, str] = {}
    if not isinstance(dry_run, bool):
        errors["dry_run"] = "dry_run must be a boolean."
    if limit in ("", None):
        limit = None
    elif not isinstance(limit, int) or limit <= 0:
        errors["limit"] = "limit must be a positive integer."

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    run_id = uuid4().hex
    command_kwargs = {
        "dry_run": dry_run,
        "limit": limit,
    }
    run = _store_video_dimension_backfill_run(
        run_id,
        run_id=run_id,
        status="queued",
        dry_run=dry_run,
        limit=limit,
        created_at=_utcnow_iso(),
        started_at=None,
        finished_at=None,
        result=None,
        error=None,
        stdout="",
    )
    _launch_video_dimension_backfill_run(run_id, command_kwargs=command_kwargs)
    return Response(
        _video_dimension_backfill_run_payload(run),
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_video_dimension_backfill_run_detail(
    request: Request, run_id: str
) -> Response:
    run = _get_video_dimension_backfill_run(run_id)
    if run is None:
        return Response(
            {"detail": "Video dimension backfill run not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(
        _video_dimension_backfill_run_payload(run),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_backup(request: Request) -> Response:
    backup_status = _backup_status_payload()
    if not backup_status.ready:
        return Response(
            {
                "detail": "Backup sources are incomplete.",
                "backup_status": backup_status.model_dump(mode="python"),
            },
            status=status.HTTP_409_CONFLICT,
        )

    target_path_raw = str(
        _request_payload(request.data).get("target_path", "") or ""
    ).strip()
    if not target_path_raw:
        return Response(
            {"errors": {"target_path": "target_path is required."}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    target_root = Path(target_path_raw).expanduser()
    if not target_root.is_absolute():
        return Response(
            {"errors": {"target_path": "target_path must be absolute."}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    resolved_target_root = target_root.resolve(strict=False)
    source_roots = [path.resolve() for path in _required_backup_sources()]
    for source_root in source_roots:
        if (
            resolved_target_root == source_root
            or source_root in resolved_target_root.parents
        ):
            return Response(
                {
                    "errors": {
                        "target_path": "target_path must not be inside the live data roots."
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = resolved_target_root / f"lx-annotate-backup-{timestamp}"

    try:
        if backup_root.exists():
            raise FileExistsError(backup_root)
        ensure_directory(backup_root)

        copied_roots: list[dict[str, Any]] = []
        for entry in backup_status.source_roots:
            source_path = Path(entry.path)
            destination = backup_root / entry.label
            copied_count = _copy_backup_source_tree(source_path, destination)
            copied_roots.append(
                {
                    "label": entry.label,
                    "source_path": str(source_path),
                    "destination_path": str(destination),
                    "file_count": copied_count,
                }
            )

        manifest = {
            "created_at": datetime.now().isoformat(),
            "target_root": str(backup_root),
            "copied_roots": copied_roots,
        }
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        atomic_write_file(
            destination=backup_root / "manifest.json",
            content=[manifest_bytes],
            required_bytes=len(manifest_bytes),
        )
    except FileExistsError:
        return Response(
            {"detail": "Backup target already exists."},
            status=status.HTTP_409_CONFLICT,
        )
    except OSError as exc:
        return Response(
            {"detail": f"Backup failed: {exc}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            "target_root": str(backup_root),
            "copied_roots": copied_roots,
        },
        status=status.HTTP_201_CREATED,
    )


__all__ = [
    "application_settings_detail",
    "application_settings_centers_dropdown",
    "application_settings_processors_dropdown",
    "application_settings_annotators_dropdown",
    "application_settings_report_templates_dropdown",
    "application_settings_ai_datasets_dropdown",
    "application_settings_ai_dataset_attachments",
    "application_settings_ai_dataset_frame_bucket_distribution",
    "application_settings_ai_dataset_training_manifest",
    "application_settings_model_training_options",
    "application_settings_model_training_runs",
    "application_settings_model_training_run_detail",
    "application_settings_video_dimension_backfill_runs",
    "application_settings_video_dimension_backfill_run_detail",
    "application_settings_ai_dataset_export",
    "application_settings_ai_dataset_export_download",
    "application_settings_backup",
    "application_settings_network_nodes",
    "application_settings_network_node_detail",
    "application_settings_network_node_roles_dropdown",
]
