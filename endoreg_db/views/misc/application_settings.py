from __future__ import annotations

import json
import threading
import traceback
from collections import Counter
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from django.core.management import call_command
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.models import (
    AIDataSet,
    AIDataSetExportArtifact,
    AIModelTrainingRun,
    Center,
    EndoscopyProcessor,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    NetworkNode,
    PatientExaminationReport,
)
from endoreg_db.services.hub import (
    deployment_profile_payload,
    local_study_server_mode_enabled,
    resolve_allowed_center_id,
)
from endoreg_db.services.video_dimension_backfill import (
    VideoDimensionBackfillResult,
    backfill_anonymized_video_dimensions,
)
from endoreg_db.utils.ai.model_training.config import (
    DEFAULT_LABELSET_VERSION_TO_TRAIN,
    TRAINING_ROOT,
)
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_write_file,
    ensure_directory,
    sha256_file,
)
from endoreg_db.utils.defaults.set_default_center import (
    get_application_defaults,
    get_application_settings,
    update_application_defaults,
)
from endoreg_db.utils.paths import EXPORT_DIR, PROTECTED_DATA_ROOT, STORAGE_DIR
from endoreg_db.utils.permissions import EnvironmentAwarePermission

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

AI_DATASET_FRAME_FORMAT_STRATEGIES = {
    "preserve_dimensions_black_mask",
    "crop_to_endoscope_roi",
}

_MODEL_TRAINING_SERVER_INSTANCE_ID = uuid4().hex
MODEL_TRAINING_LOST_TIMEOUT = timedelta(hours=12)
_VIDEO_DIMENSION_BACKFILL_RUNS: dict[str, dict[str, Any]] = {}
_VIDEO_DIMENSION_BACKFILL_RUNS_LOCK = threading.Lock()


def _integer_param_error(field_name: str) -> Response:
    return Response(
        {"errors": {field_name: f"{field_name} must be an integer."}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _parse_optional_integer_param(
    raw_value: object,
    *,
    field_name: str,
) -> tuple[int | None, Response | None]:
    if raw_value in (None, ""):
        return None, None
    if isinstance(raw_value, bool) or not isinstance(
        raw_value, (str, bytes, bytearray, int)
    ):
        return None, _integer_param_error(field_name)
    try:
        return int(raw_value), None
    except ValueError:
        return None, _integer_param_error(field_name)


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


def _backup_status_payload() -> dict[str, Any]:
    required_sources = [path.resolve() for path in _required_backup_sources()]
    missing_paths = [str(path) for path in required_sources if not path.exists()]
    source_roots = [
        {
            "label": _backup_source_label(index, path),
            "path": str(path),
            "exists": path.exists(),
            "file_count": _count_files(path) if path.exists() else 0,
        }
        for index, path in enumerate(required_sources)
    ]
    return {
        "ready": len(missing_paths) == 0,
        "missing_paths": missing_paths,
        "required_path_count": len(required_sources),
        "available_path_count": len(required_sources) - len(missing_paths),
        "source_roots": source_roots,
    }


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


def _settings_payload(request) -> dict[str, Any]:
    settings_obj = get_application_settings()
    snapshot = get_application_defaults()
    annotator_name = snapshot.annotator_name
    if (
        not annotator_name
        and getattr(request, "user", None)
        and request.user.is_authenticated
    ):
        annotator_name = str(request.user.username or "")
    return {
        "id": settings_obj.pk,
        "center_id": snapshot.center_id,
        "center_name": snapshot.center_name,
        "processor_id": snapshot.processor_id,
        "processor_name": snapshot.processor_name,
        "annotator_name": annotator_name,
        "report_template_name": snapshot.report_template_name,
        "ai_dataset_name": snapshot.ai_dataset_name,
        "ai_dataset_type": snapshot.ai_dataset_type,
        "updated_at": (
            settings_obj.updated_at.isoformat() if settings_obj.updated_at else None
        ),
        "deployment_profile": deployment_profile_payload(),
        "backup_status": _backup_status_payload(),
    }


def _application_settings_ai_dataset_entries() -> list[dict[str, Any]]:
    dataset_counts = Counter(
        AIDataSet.objects.exclude(name__exact="").values_list("name", flat=True)
    )
    entries = []
    for dataset in AIDataSet.objects.exclude(name__exact="").order_by(
        "name", "dataset_type", "pk"
    ):
        entries.append(
            {
                "id": dataset.pk,
                "value": dataset.name,
                "label": dataset.name,
                "dataset_type": dataset.dataset_type,
                "ai_model_type": dataset.ai_model_type,
                "is_active": dataset.is_active,
                "name_count": dataset_counts.get(dataset.name, 1),
            }
        )
    return entries


def _resolve_ai_dataset_param(param: object) -> AIDataSet | None:
    normalized = str(param or "").strip()
    if not normalized:
        return None
    if normalized.isdecimal():
        dataset = AIDataSet.objects.filter(pk=int(normalized)).first()
        if dataset is not None:
            return dataset
    return AIDataSet.objects.filter(name=normalized).order_by("pk").first()


def _resolve_label_set_for_distribution(
    raw_value: object,
) -> tuple[LabelSet | None, Response | None]:
    label_group_id, error = _parse_optional_integer_param(
        raw_value,
        field_name="label_group_id",
    )
    if error is not None:
        return None, error
    if label_group_id is None:
        return None, None

    label_set = LabelSet.objects.filter(pk=label_group_id).first()
    if label_set is None:
        return None, Response(
            {
                "errors": {
                    "label_group_id": f"Unknown label_group_id: {label_group_id}."
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )
    return label_set, None


def _resolve_target_label_for_distribution(
    *,
    label_set: LabelSet | None,
    target_label_id_raw: object,
    target_label_name_raw: object,
) -> tuple[Label | None, Response | None]:
    if target_label_id_raw in {None, ""} and target_label_name_raw in {None, ""}:
        return None, None

    labels = Label.objects.all()
    if label_set is not None:
        labels = labels.filter(label_sets=label_set)

    target_label_id, error = _parse_optional_integer_param(
        target_label_id_raw,
        field_name="target_label_id",
    )
    if error is not None:
        return None, error
    if target_label_id is not None:
        label = labels.filter(pk=target_label_id).first()
        if label is None:
            return None, Response(
                {
                    "errors": {
                        "target_label_id": f"Unknown target_label_id: {target_label_id}."
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return label, None

    target_label_name = str(target_label_name_raw or "").strip()
    if not target_label_name:
        return None, None
    label = labels.filter(name=target_label_name).first()
    if label is None:
        label = labels.filter(name__iexact=target_label_name).first()
    if label is None:
        return None, Response(
            {"errors": {"target_label": f"Unknown target_label: {target_label_name}."}},
            status=status.HTTP_404_NOT_FOUND,
        )
    return label, None


def _payload_bool_field(
    payload: dict[str, Any],
    field_name: str,
    *,
    default: bool,
) -> tuple[bool, Response | None]:
    raw_value = payload.get(field_name, default)
    if isinstance(raw_value, bool):
        return raw_value, None
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True, None
        if normalized in {"0", "false", "no", "off"}:
            return False, None
    return (
        default,
        Response(
            {"errors": {field_name: f"{field_name} must be a boolean."}},
            status=status.HTTP_400_BAD_REQUEST,
        ),
    )


def s, try_payload_strategy_field(
    payload: dict[str, Any],
    field_name: str,
    *,
    default: str,
) -> AIFrameFormatStrategy:
    raw_value = payload.get(field_name, default)
    if not isinstance(raw_value, str):
        return (
            default,
            Response(
                {"errors": {field_name: f"{field_name} must be a string."}},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )
    normalized = raw_value.strip() or default
    if normalized not in AI_DATASET_FRAME_FORMAT_STRATEGIES:
        allowed = ", ".join(sorted(AI_DATASET_FRAME_FORMAT_STRATEGIES))
        return (
            default,
            Response(
                {"errors": {field_name: f"{field_name} must be one of: {allowed}."}},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        )
    return normalized, None


def _payload_information_source_names(
    raw_value: object,
) -> tuple[list[str] | None, Response | None]:
    if raw_value in (None, ""):
        return None, None
    if isinstance(raw_value, str):
        names = [name.strip() for name in raw_value.split(",") if name.strip()]
        return names or None, None
    if isinstance(raw_value, list):
        names: list[str] = []
        for item in raw_value:
            if not isinstance(item, str):
                return (
                    None,
                    Response(
                        {
                            "errors": {
                                "information_source_names": (
                                    "information_source_names entries must be strings."
                                )
                            }
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    ),
                )
            stripped = item.strip()
            if stripped:
                names.append(stripped)
        return names or None, None
    return (
        None,
        Response(
            {
                "errors": {
                    "information_source_names": (
                        "information_source_names must be a string or list of strings."
                    )
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        ),
    )


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


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


def _mark_lost_model_training_runs() -> None:
    now = timezone.now()
    stale_before = now - MODEL_TRAINING_LOST_TIMEOUT
    AIModelTrainingRun.objects.filter(
        status__in=[
            AIModelTrainingRun.STATUS_QUEUED,
            AIModelTrainingRun.STATUS_RUNNING,
        ],
        updated_at__lt=stale_before,
    ).exclude(server_instance_id=_MODEL_TRAINING_SERVER_INSTANCE_ID).update(
        status=AIModelTrainingRun.STATUS_LOST,
        finished_at=now,
        error=(
            "Training run remained queued/running without an update after "
            "backend process ownership changed. Marked LOST so the result is "
            "not silently hidden."
        ),
    )


def _parse_model_training_result(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _model_training_artifact_paths(result: dict[str, Any] | None) -> dict[str, str]:
    if not result:
        return {}
    paths: dict[str, str] = {}
    for key in (
        "model_path",
        "manifest_path",
        "meta_path",
        "training_result_path",
        "checkpoint_path",
        "onnx_path",
    ):
        value = result.get(key)
        if isinstance(value, str) and value:
            paths[key] = value
    training_result = result.get("training_result")
    if isinstance(training_result, dict):
        for artifact in training_result.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            kind = str(artifact.get("kind") or "").strip().lower()
            path = artifact.get("path")
            if kind and isinstance(path, str) and path:
                paths[f"{kind}_path"] = path
    return paths


def _model_training_run_payload(run: AIModelTrainingRun) -> dict[str, Any]:
    request_payload = run.request_payload or {}
    training_target = request_payload.get("training_target")
    if training_target not in {
        MODEL_TRAINING_TARGET_IMAGE_MULTILABEL,
        MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR,
    }:
        training_target = (
            MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR
            if run.ai_model_type == MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR
            else MODEL_TRAINING_TARGET_IMAGE_MULTILABEL
        )

    return {
        "run_id": run.run_key,
        "training_target": training_target,
        "status": run.status,
        "dataset_id": run.dataset_id,
        "dataset_name": run.dataset_name,
        "dataset_type": run.dataset_type,
        "ai_model_type": run.ai_model_type,
        "backbone_name": run.backbone_name,
        "feature_mode": run.feature_mode,
        "freeze_backbone": run.freeze_backbone,
        "epochs": run.epochs,
        "batch_size": run.batch_size,
        "labelset_version": run.labelset_version,
        "treat_unlabeled_as_negative": run.treat_unlabeled_as_negative,
        "backbone_checkpoint": run.backbone_checkpoint,
        "created_at": _isoformat(run.created_at),
        "started_at": _isoformat(run.started_at),
        "finished_at": _isoformat(run.finished_at),
        "result": run.result,
        "artifact_paths": run.artifact_paths,
        "error": run.error or None,
        "stdout": run.stdout,
        "stderr": run.stderr,
    }


def _execute_model_training_run(
    run_id: str,
    *,
    command_kwargs: dict[str, Any],
) -> None:
    run_uuid = _coerce_uuid(run_id)
    if run_uuid is None:
        return
    AIModelTrainingRun.objects.filter(run_id=run_uuid).update(
        status=AIModelTrainingRun.STATUS_RUNNING,
        started_at=timezone.now(),
        server_instance_id=_MODEL_TRAINING_SERVER_INSTANCE_ID,
    )
    stdout = StringIO()
    stderr = StringIO()
    try:
        command_name = str(
            command_kwargs.get("_command_name") or "train_image_multilabel_model"
        )
        command_options = {
            key: value
            for key, value in command_kwargs.items()
            if not key.startswith("_")
        }
        call_command(
            command_name,
            stdout=stdout,
            stderr=stderr,
            **command_options,
        )
        output = stdout.getvalue()
        error_output = stderr.getvalue()
        result = _parse_model_training_result(output)
        artifact_paths = _model_training_artifact_paths(result)
        AIModelTrainingRun.objects.filter(run_id=run_uuid).update(
            status=AIModelTrainingRun.STATUS_COMPLETED,
            finished_at=timezone.now(),
            stdout=output,
            stderr=error_output,
            result=result,
            artifact_paths=artifact_paths,
            error="",
        )
    except Exception as exc:
        output = stdout.getvalue()
        error_output = stderr.getvalue()
        trace = traceback.format_exc()
        combined_output = "\n".join(
            chunk for chunk in (output, error_output, trace) if chunk
        ).strip()
        AIModelTrainingRun.objects.filter(run_id=run_uuid).update(
            status=AIModelTrainingRun.STATUS_FAILED,
            finished_at=timezone.now(),
            stdout=combined_output,
            stderr=error_output,
            error=str(exc),
            result=None,
            artifact_paths={},
        )


def _launch_model_training_run(run_id: str, *, command_kwargs: dict[str, Any]) -> None:
    thread = threading.Thread(
        target=_execute_model_training_run,
        kwargs={"run_id": run_id, "command_kwargs": command_kwargs},
        daemon=True,
    )
    thread.start()


def _create_phi_region_detector_training_run(payload: dict[str, Any]) -> Response:
    dataset_yaml, dataset_yaml_error = _coerce_local_training_path(
        payload.get("dataset_yaml"),
        field_name="dataset_yaml",
    )
    output_dir, output_dir_error = _coerce_local_training_path(
        payload.get("output_dir"),
        field_name="output_dir",
        required=False,
    )
    if output_dir is None:
        output_dir = str((TRAINING_ROOT / "phi_region_detector").resolve())

    base_model = str(payload.get("base_model", "yolov8n.pt") or "").strip()
    run_name_raw = payload.get("run_name")
    run_name = (
        str(run_name_raw).strip()
        if isinstance(run_name_raw, str) and str(run_name_raw).strip()
        else None
    )
    epochs = payload.get("epochs", 50)
    batch_size = payload.get("batch_size", 16)
    input_size = payload.get("input_size", 640)
    device = str(payload.get("device", "auto") or "auto").strip() or "auto"
    workers = payload.get("workers", 4)
    patience = payload.get("patience", 25)
    export_onnx = payload.get("export_onnx", True)
    confidence_threshold = payload.get("confidence_threshold", 0.35)
    nms_threshold = payload.get("nms_threshold", 0.45)
    class_ids = str(payload.get("class_ids", "") or "").strip()

    errors: dict[str, str] = {}
    if dataset_yaml_error:
        errors["dataset_yaml"] = dataset_yaml_error
    if output_dir_error:
        errors["output_dir"] = output_dir_error
    if not base_model:
        errors["base_model"] = "base_model is required."
    if not isinstance(epochs, int) or epochs <= 0:
        errors["epochs"] = "epochs must be a positive integer."
    if not isinstance(batch_size, int) or batch_size <= 0:
        errors["batch_size"] = "batch_size must be a positive integer."
    if not isinstance(input_size, int) or input_size < 32:
        errors["input_size"] = "input_size must be an integer >= 32."
    if not isinstance(workers, int) or workers < 0:
        errors["workers"] = "workers must be an integer >= 0."
    if not isinstance(patience, int) or patience < 0:
        errors["patience"] = "patience must be an integer >= 0."
    if not isinstance(export_onnx, bool):
        errors["export_onnx"] = "export_onnx must be a boolean."
    if not isinstance(confidence_threshold, (int, float)) or not (
        0.0 <= float(confidence_threshold) <= 1.0
    ):
        errors["confidence_threshold"] = "confidence_threshold must be between 0 and 1."
    if not isinstance(nms_threshold, (int, float)) or not (
        0.0 <= float(nms_threshold) <= 1.0
    ):
        errors["nms_threshold"] = "nms_threshold must be between 0 and 1."

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    command_kwargs = {
        "_command_name": "train_phi_region_detector",
        "dataset_yaml": dataset_yaml,
        "output_dir": output_dir,
        "base_model": base_model,
        "run_name": run_name,
        "epochs": epochs,
        "batch_size": batch_size,
        "input_size": input_size,
        "device": device,
        "workers": workers,
        "patience": patience,
        "export_onnx": export_onnx,
        "confidence_threshold": float(confidence_threshold),
        "nms_threshold": float(nms_threshold),
        "class_ids": class_ids,
    }
    run = AIModelTrainingRun.objects.create(
        dataset=None,
        dataset_name=Path(dataset_yaml).name
        if dataset_yaml
        else "PHI detector dataset",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR,
        backbone_name=base_model,
        feature_mode="yolo_onnx_detector",
        freeze_backbone=False,
        epochs=epochs,
        batch_size=batch_size,
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


def _network_node_payload(node: NetworkNode) -> dict[str, Any]:
    owning_center = node.owning_center
    owning_center_id = owning_center.pk if owning_center is not None else None
    try:
        role_label = NetworkNode.Role(node.role).label
    except ValueError:
        role_label = node.role

    return {
        "id": node.pk,
        "node_key": node.node_key,
        "display_name": node.display_name,
        "role": node.role,
        "role_label": role_label,
        "base_url": node.base_url,
        "is_active": node.is_active,
        "owning_center_id": owning_center_id,
        "owning_center_key": (
            owning_center.center_key if owning_center is not None else None
        ),
        "owning_center_name": owning_center.name if owning_center is not None else None,
        "has_shared_secret": bool(node.shared_secret_hash),
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


def _resolve_center_from_payload(
    data: dict[str, Any],
    *,
    errors: dict[str, str],
) -> Center | None | object:
    sentinel = object()
    if "owning_center_id" not in data and "owning_center_key" not in data:
        return sentinel

    center_value = data.get("owning_center_id", data.get("owning_center_key"))
    if center_value in (None, "", 0):
        return None

    if isinstance(center_value, int):
        center = Center.objects.filter(pk=center_value).first()
    else:
        center = Center.objects.filter(center_key=str(center_value).strip()).first()

    if center is None:
        errors["owning_center"] = "Owning center not found."
    return center


def _network_node_roles_payload() -> list[dict[str, str]]:
    return [
        {"value": choice.value, "label": str(choice.label)}
        for choice in NetworkNode.Role
    ]


@api_view(["GET", "PATCH"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_detail(request):
    if request.method == "GET":
        return Response(_settings_payload(request), status=status.HTTP_200_OK)

    data = request.data
    center_value = data.get("center_id", data.get("center_name"))
    processor_value = data.get("processor_id", data.get("processor_name"))
    annotator_name = data.get("annotator_name")
    report_template_name = data.get("report_template_name")
    ai_dataset_name = data.get("ai_dataset_name")
    ai_dataset_type = data.get("ai_dataset_type")

    if "annotator_name" in data and annotator_name is None:
        annotator_name = ""
    if "report_template_name" in data and report_template_name is None:
        report_template_name = ""
    if "ai_dataset_name" in data and ai_dataset_name is None:
        ai_dataset_name = ""
    if "ai_dataset_type" in data and ai_dataset_type is None:
        ai_dataset_type = ""

    errors: dict[str, str] = {}
    if "center_id" in data or "center_name" in data:
        if center_value not in (None, "", 0):
            center_exists = (
                Center.objects.filter(pk=center_value).exists()
                if isinstance(center_value, int)
                else Center.objects.filter(name=center_value).exists()
            )
            if not center_exists:
                errors["center"] = "Center not found."
            else:
                pass
        if center_value in ("", 0):
            center_value = None

    if "processor_id" in data or "processor_name" in data:
        if processor_value not in (None, "", 0):
            processor_exists = (
                EndoscopyProcessor.objects.filter(pk=processor_value).exists()
                if isinstance(processor_value, int)
                else EndoscopyProcessor.objects.filter(name=processor_value).exists()
            )
            if not processor_exists:
                errors["processor"] = "Processor not found."
        if processor_value in ("", 0):
            processor_value = None

    if annotator_name is not None and not isinstance(annotator_name, str):
        errors["annotator_name"] = "annotator_name must be a string."
    if report_template_name is not None and not isinstance(report_template_name, str):
        errors["report_template_name"] = "report_template_name must be a string."
    if ai_dataset_name is not None and not isinstance(ai_dataset_name, str):
        errors["ai_dataset_name"] = "ai_dataset_name must be a string."
    if ai_dataset_type is not None:
        if not isinstance(ai_dataset_type, str):
            errors["ai_dataset_type"] = "ai_dataset_type must be a string."
        elif ai_dataset_type not in {
            "",
            AIDataSet.DATASET_TYPE_IMAGE,
            AIDataSet.DATASET_TYPE_VIDEO,
        }:
            errors["ai_dataset_type"] = "ai_dataset_type must be one of: image, video."

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    update_application_defaults(
        center=center_value if ("center_id" in data or "center_name" in data) else None,
        processor=(
            processor_value
            if ("processor_id" in data or "processor_name" in data)
            else None
        ),
        annotator_name=annotator_name,
        report_template_name=report_template_name,
        ai_dataset_name=ai_dataset_name,
        ai_dataset_type=ai_dataset_type,
    )
    return Response(_settings_payload(request), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_centers_dropdown(request):
    centers = Center.objects.order_by("name").values("id", "name")
    return Response(list(centers), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_processors_dropdown(request):
    processors = EndoscopyProcessor.objects.order_by("name").values("id", "name")
    return Response(list(processors), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_annotators_dropdown(request):
    values = list(
        ImageClassificationAnnotation.objects.exclude(annotator__isnull=True)
        .exclude(annotator__exact="")
        .order_by("annotator")
        .values_list("annotator", flat=True)
        .distinct()
    )
    current_value = get_application_settings().annotator_name
    if current_value and current_value not in values:
        values.insert(0, current_value)
    return Response(
        [{"value": value, "label": value} for value in values],
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_report_templates_dropdown(request):
    values = list(
        PatientExaminationReport.objects.exclude(template_name__exact="")
        .order_by("template_name")
        .values_list("template_name", flat=True)
        .distinct()
    )
    current_value = get_application_settings().report_template_name
    if current_value and current_value not in values:
        values.insert(0, current_value)
    return Response(
        [{"value": value, "label": value} for value in values],
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_datasets_dropdown(request):
    return Response(
        _application_settings_ai_dataset_entries(),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_frame_bucket_distribution(request, param: str):
    dataset = _resolve_ai_dataset_param(param)
    if dataset is None:
        return Response(
            {"detail": f"AIDataSet {param} was not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    label_set, error = _resolve_label_set_for_distribution(
        request.query_params.get(
            "label_group_id",
            request.query_params.get("label_set_id"),
        )
    )
    if error is not None:
        return error

    target_label, error = _resolve_target_label_for_distribution(
        label_set=label_set,
        target_label_id_raw=request.query_params.get("target_label_id"),
        target_label_name_raw=request.query_params.get("target_label"),
    )
    if error is not None:
        return error

    prediction_segments_only = _payload_bool(
        request.query_params.get("prediction_segments_only"),
        default=True,
    )
    distribution = dataset.build_frame_bucket_distribution(
        label_set=label_set,
        target_label=target_label,
        prediction_segments_only=prediction_segments_only,
    )
    return Response(distribution.model_dump(mode="json"), status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_training_manifest(request, param: str):
    dataset = _resolve_ai_dataset_param(param)
    if dataset is None:
        return Response(
            {"detail": f"AIDataSet {param} was not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    payload: dict[str, Any] = request.data or {}
    label_set_id, error = _parse_optional_integer_param(
        payload.get("label_set_id"),
        field_name="label_set_id",
    )
    if error is not None:
        return error

    label_set = None
    if label_set_id is not None:
        label_set = LabelSet.objects.filter(pk=label_set_id).first()
        if label_set is None:
            return Response(
                {"errors": {"label_set_id": f"Unknown label_set_id: {label_set_id}."}},
                status=status.HTTP_404_NOT_FOUND,
            )

    treat_unlabeled_as_negative, error = _payload_bool_field(
        payload,
        "treat_unlabeled_as_negative",
        default=False,
    )
    if error is not None:
        return error
    include_file_paths, error = _payload_bool_field(
        payload,
        "include_file_paths",
        default=False,
    )
    if error is not None:
        return error
    check_frame_format, error = _payload_bool_field(
        payload,
        "check_frame_format",
        default=True,
    )
    if error is not None:
        return error

    preprocessing_strategy, error = _payload_strategy_field(
        payload,
        "preprocessing_strategy",
        default="preserve_dimensions_black_mask",
    )
    if error is not None:
        return error
    recommended_model_input_strategy, error = _payload_strategy_field(
        payload,
        "recommended_model_input_strategy",
        default="crop_to_endoscope_roi",
    )
    if error is not None:
        return error

    information_source_names, error = _payload_information_source_names(
        payload.get("information_source_names")
    )
    if error is not None:
        return error

    try:
        manifest = dataset.build_frame_multilabel_training_manifest(
            label_set=label_set,
            treat_unlabeled_as_negative=treat_unlabeled_as_negative,
            include_file_paths=include_file_paths,
            check_frame_format=check_frame_format,
            preprocessing_strategy=preprocessing_strategy,
            recommended_model_input_strategy=recommended_model_input_strategy,
            information_source_names=information_source_names,
        )
    except ValueError as exc:
        return Response(
            {"errors": {"manifest": str(exc)}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    manifest_payload = manifest.model_dump(mode="json")
    return Response(
        {
            "dataset_id": dataset.pk,
            "dataset_name": dataset.name,
            "dataset_type": dataset.dataset_type,
            "ai_model_type": dataset.ai_model_type,
            "config": {
                "label_set_id": label_set.pk if label_set is not None else None,
                "treat_unlabeled_as_negative": treat_unlabeled_as_negative,
                "include_file_paths": include_file_paths,
                "check_frame_format": check_frame_format,
                "preprocessing_strategy": preprocessing_strategy,
                "recommended_model_input_strategy": recommended_model_input_strategy,
                "information_source_names": information_source_names,
            },
            "summary": {
                "label_count": len(manifest.labels),
                "sample_count": len(manifest.samples),
                "class_frequencies": manifest.class_frequencies,
                "frame_format": manifest.frame_format.model_dump(mode="json"),
            },
            "manifest": manifest_payload,
            "lx_ai_core_manifest": manifest.to_lx_ai_core_dict(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_options(request):
    return Response(
        {
            "training_targets": list(MODEL_TRAINING_TARGET_OPTIONS),
            "ai_datasets": [
                entry
                for entry in _application_settings_ai_dataset_entries()
                if entry["dataset_type"] == AIDataSet.DATASET_TYPE_IMAGE
                and entry["ai_model_type"] == AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL
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
                "treat_unlabeled_as_negative": True,
                "backbone_checkpoint": None,
            },
        },
        status=status.HTTP_200_OK,
    )


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_runs(request):
    _mark_lost_model_training_runs()

    if request.method == "GET":
        runs = AIModelTrainingRun.objects.select_related("dataset").order_by(
            "-created_at",
            "-id",
        )[:25]
        return Response(
            [_model_training_run_payload(run) for run in runs],
            status=status.HTTP_200_OK,
        )

    payload: dict[str, Any] = request.data or {}
    training_target = str(
        payload.get("training_target", MODEL_TRAINING_TARGET_IMAGE_MULTILABEL) or ""
    ).strip()
    if training_target == MODEL_TRAINING_TARGET_PHI_REGION_DETECTOR:
        return _create_phi_region_detector_training_run(payload)
    if training_target != MODEL_TRAINING_TARGET_IMAGE_MULTILABEL:
        return Response(
            {"errors": {"training_target": "Unsupported training_target."}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    dataset_id = payload.get("dataset_id")
    backbone_name = str(payload.get("backbone_name", "gastro_rn50") or "").strip()
    feature_mode = str(payload.get("feature_mode", "freeze_backbone") or "").strip()
    backbone_checkpoint_raw = payload.get("backbone_checkpoint")
    epochs = payload.get("epochs", 10)
    batch_size = payload.get("batch_size", 32)
    labelset_version = payload.get(
        "labelset_version",
        DEFAULT_LABELSET_VERSION_TO_TRAIN,
    )
    treat_unlabeled_as_negative = payload.get("treat_unlabeled_as_negative", True)

    errors: dict[str, str] = {}
    if not isinstance(dataset_id, int):
        errors["dataset_id"] = "dataset_id must be an integer."
    if backbone_name not in {
        option["value"] for option in MODEL_TRAINING_BACKBONE_OPTIONS
    }:
        errors["backbone_name"] = "Unsupported backbone_name."
    if feature_mode not in {
        option["value"] for option in MODEL_TRAINING_FEATURE_MODE_OPTIONS
    }:
        errors["feature_mode"] = "Unsupported feature_mode."
    if not isinstance(epochs, int) or epochs <= 0:
        errors["epochs"] = "epochs must be a positive integer."
    if not isinstance(batch_size, int) or batch_size <= 0:
        errors["batch_size"] = "batch_size must be a positive integer."
    if not isinstance(labelset_version, int) or labelset_version <= 0:
        errors["labelset_version"] = "labelset_version must be a positive integer."
    if not isinstance(treat_unlabeled_as_negative, bool):
        errors["treat_unlabeled_as_negative"] = (
            "treat_unlabeled_as_negative must be a boolean."
        )

    backbone_checkpoint: str | None = None
    if backbone_checkpoint_raw not in (None, ""):
        if not isinstance(backbone_checkpoint_raw, str):
            errors["backbone_checkpoint"] = "backbone_checkpoint must be a string."
        else:
            backbone_checkpoint = backbone_checkpoint_raw.strip() or None

    dataset = None
    if not errors:
        dataset = AIDataSet.objects.filter(pk=dataset_id).first()
        if dataset is None:
            errors["dataset_id"] = "AIDataSet not found."
        elif dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE:
            errors["dataset_id"] = "AIDataSet must have dataset_type='image'."
        elif dataset.ai_model_type != AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL:
            errors["dataset_id"] = (
                "AIDataSet must have ai_model_type='image_multilabel_classification'."
            )

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    freeze_backbone = feature_mode == "freeze_backbone"
    command_kwargs = {
        "_command_name": "train_image_multilabel_model",
        "dataset_id": dataset.pk,
        "backbone_name": backbone_name,
        "backbone_checkpoint": backbone_checkpoint,
        "epochs": epochs,
        "batch_size": batch_size,
        "labelset_version": labelset_version,
        "freeze_backbone": freeze_backbone,
        "treat_unlabeled_as_negative": treat_unlabeled_as_negative,
    }
    run = AIModelTrainingRun.objects.create(
        dataset=dataset,
        dataset_name=dataset.name,
        dataset_type=dataset.dataset_type,
        ai_model_type=dataset.ai_model_type,
        backbone_name=backbone_name,
        feature_mode=feature_mode,
        freeze_backbone=freeze_backbone,
        epochs=epochs,
        batch_size=batch_size,
        labelset_version=labelset_version,
        treat_unlabeled_as_negative=treat_unlabeled_as_negative,
        backbone_checkpoint=backbone_checkpoint,
        request_payload=payload,
        command_kwargs=command_kwargs,
        status=AIModelTrainingRun.STATUS_QUEUED,
        server_instance_id=_MODEL_TRAINING_SERVER_INSTANCE_ID,
    )
    _launch_model_training_run(run.run_key, command_kwargs=command_kwargs)
    return Response(_model_training_run_payload(run), status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_run_detail(request, run_id: str):
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
def application_settings_video_dimension_backfill_runs(request):
    payload: dict[str, Any] = request.data or {}
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
def application_settings_video_dimension_backfill_run_detail(request, run_id: str):
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


def _sanitize_export_token(value: str) -> str:
    normalized = []
    for char in value.strip():
        if char.isalnum():
            normalized.append(char.lower())
        elif char in {"-", "_"}:
            normalized.append(char)
        else:
            normalized.append("_")
    collapsed = "".join(normalized).strip("_")
    return collapsed or "dataset"


def _payload_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _dataset_export_scope_error(
    request,
    *,
    center_key: str | None,
    all_centers: bool,
    only_validated: bool,
) -> tuple[str, int] | None:
    if center_key and all_centers:
        return "Export scope must use center_key or all_centers, not both.", 400

    local_study_server = local_study_server_mode_enabled()
    if not local_study_server:
        return None

    user = getattr(request, "user", None)
    authenticated = bool(user and getattr(user, "is_authenticated", False))
    privileged = bool(
        authenticated
        and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))
    )
    if not authenticated:
        return "Authentication is required for local_study_server exports.", 403
    if not (bool(center_key) ^ all_centers):
        return (
            "local_study_server exports require exactly one center scope: "
            "center_key or all_centers.",
            400,
        )
    if all_centers and not privileged:
        return "all_centers export requires staff or superuser privileges.", 403
    if not only_validated:
        return "local_study_server exports require only_validated=true.", 400
    if center_key:
        center = Center.objects.filter(center_key=center_key).first()
        if center is None:
            return f"Unknown center_key: {center_key}", 400
        allowed_center_id = resolve_allowed_center_id(user)
        if allowed_center_id == -1:
            return "You do not have access to export center data.", 403
        if allowed_center_id is not None and center.id != allowed_center_id:
            return "Export center is outside the authenticated scope.", 403

    return None


def _resolve_ai_dataset_export_dataset(
    payload: dict[str, Any],
) -> tuple[AIDataSet | None, Response | None]:
    settings_obj = get_application_settings()
    normalized_dataset_id, dataset_id_error = _parse_optional_integer_param(
        payload.get("dataset_id"),
        field_name="dataset_id",
    )

    if dataset_id_error is not None:
        return None, dataset_id_error
    if normalized_dataset_id is not None:
        dataset = AIDataSet.objects.filter(pk=normalized_dataset_id).first()
        if dataset is None:
            return None, Response(
                {"errors": {"dataset_id": "AIDataSet not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return dataset, None

    dataset_name = payload.get("ai_dataset_name", settings_obj.ai_dataset_name)
    dataset_type = payload.get("ai_dataset_type", settings_obj.ai_dataset_type)

    errors: dict[str, str] = {}
    if not isinstance(dataset_name, str) or not dataset_name.strip():
        errors["ai_dataset_name"] = "ai_dataset_name is required."
    if not isinstance(dataset_type, str) or dataset_type not in {
        AIDataSet.DATASET_TYPE_IMAGE,
        AIDataSet.DATASET_TYPE_VIDEO,
    }:
        errors["ai_dataset_type"] = "ai_dataset_type must be one of: image, video."
    if errors:
        return None, Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    matches = list(
        AIDataSet.objects.filter(
            name=dataset_name.strip(),
            dataset_type=dataset_type,
        ).order_by("-updated_at", "-pk")[:2]
    )
    if not matches:
        return None, Response(
            {
                "errors": {
                    "ai_dataset_name": (
                        f"No AIDataSet found for name='{dataset_name.strip()}' "
                        f"and dataset_type='{dataset_type}'."
                    )
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )
    if len(matches) > 1:
        return None, Response(
            {
                "errors": {
                    "ai_dataset_name": (
                        "Multiple AIDataSet rows match this name/type. "
                        "Export by dataset_id to avoid selecting the wrong dataset."
                    )
                }
            },
            status=status.HTTP_409_CONFLICT,
        )
    return matches[0], None


def _ai_dataset_export_download_url(artifact: AIDataSetExportArtifact) -> str:
    return (
        f"/api/settings/application/ai_dataset_export/{artifact.artifact_key}/download/"
    )


def _ai_dataset_export_payload(artifact: AIDataSetExportArtifact) -> dict[str, Any]:
    return {
        "success": artifact.status == AIDataSetExportArtifact.STATUS_COMPLETED,
        "artifact_id": artifact.artifact_key,
        "dataset_id": artifact.dataset_id,
        "dataset_name": artifact.dataset_name,
        "dataset_type": artifact.dataset_type,
        "output_path": artifact.output_path,
        "download_url": (
            _ai_dataset_export_download_url(artifact)
            if artifact.status == AIDataSetExportArtifact.STATUS_COMPLETED
            else None
        ),
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "summary": artifact.summary,
        "status": artifact.status,
        "error": artifact.error or None,
    }


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_export(request):
    payload: dict[str, Any] = request.data or {}
    dataset, dataset_error = _resolve_ai_dataset_export_dataset(payload)
    if dataset_error is not None:
        return dataset_error
    assert dataset is not None

    center_key = str(payload.get("center_key") or "").strip() or None
    all_centers = _payload_bool(payload.get("all_centers"), default=False)
    only_validated = _payload_bool(payload.get("only_validated"), default=True)
    scope_error = _dataset_export_scope_error(
        request,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
    )
    if scope_error is not None:
        error_message, status_code = scope_error
        return Response({"success": False, "error": error_message}, status=status_code)

    artifact = AIDataSetExportArtifact.objects.create(
        dataset=dataset,
        dataset_name=dataset.name,
        dataset_type=dataset.dataset_type,
        ai_model_type=dataset.ai_model_type,
        request_payload=payload,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
        status=AIDataSetExportArtifact.STATUS_RUNNING,
    )

    export_dir = EXPORT_DIR / "ai_datasets"
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    file_name = (
        f"{_sanitize_export_token(dataset.name or 'dataset')}"
        f"_{_sanitize_export_token(dataset.dataset_type)}"
        f"_{timestamp}_{artifact.artifact_key}.json"
    )
    output_path = export_dir / file_name

    try:
        export_payload = dataset.export_to_standardized_structure(
            center_key=center_key,
            all_centers=all_centers,
            only_validated=only_validated,
        )
        json_bytes = json.dumps(
            export_payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")
        atomic_write_file(
            destination=output_path,
            content=[json_bytes],
            required_bytes=len(json_bytes),
        )
        artifact.status = AIDataSetExportArtifact.STATUS_COMPLETED
        artifact.output_path = str(output_path)
        artifact.download_filename = file_name
        artifact.sha256 = sha256_file(output_path)
        artifact.byte_size = len(json_bytes)
        artifact.summary = export_payload.get("summary", {})
        artifact.error = ""
        artifact.finished_at = timezone.now()
        artifact.save(
            update_fields=[
                "status",
                "output_path",
                "download_filename",
                "sha256",
                "byte_size",
                "summary",
                "error",
                "finished_at",
                "updated_at",
            ]
        )
    except Exception as exc:
        artifact.status = AIDataSetExportArtifact.STATUS_FAILED
        artifact.error = str(exc)
        artifact.finished_at = timezone.now()
        artifact.save(update_fields=["status", "error", "finished_at", "updated_at"])
        return Response(
            _ai_dataset_export_payload(artifact),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        _ai_dataset_export_payload(artifact),
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_export_download(request, artifact_id: str):
    artifact_uuid = _coerce_uuid(artifact_id)
    artifact = (
        AIDataSetExportArtifact.objects.filter(artifact_id=artifact_uuid).first()
        if artifact_uuid is not None
        else None
    )
    if artifact is None:
        return Response(
            {"detail": "AI dataset export artifact not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    if artifact.status != AIDataSetExportArtifact.STATUS_COMPLETED:
        return Response(
            _ai_dataset_export_payload(artifact),
            status=status.HTTP_409_CONFLICT,
        )

    output_path = Path(artifact.output_path)
    try:
        output_path.resolve().relative_to(EXPORT_DIR.resolve())
    except ValueError:
        artifact.status = AIDataSetExportArtifact.STATUS_FAILED
        artifact.error = "Export artifact path is outside the configured export root."
        artifact.finished_at = timezone.now()
        artifact.save(update_fields=["status", "error", "finished_at", "updated_at"])
        return Response(
            _ai_dataset_export_payload(artifact),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not output_path.is_file():
        artifact.status = AIDataSetExportArtifact.STATUS_FAILED
        artifact.error = "Export artifact file is missing from disk."
        artifact.finished_at = timezone.now()
        artifact.save(update_fields=["status", "error", "finished_at", "updated_at"])
        return Response(
            _ai_dataset_export_payload(artifact),
            status=status.HTTP_410_GONE,
        )

    response = FileResponse(
        output_path.open("rb"),
        as_attachment=True,
        filename=artifact.download_filename or output_path.name,
        content_type="application/json",
    )
    response["X-Content-SHA256"] = artifact.sha256
    response["X-Content-Length"] = str(artifact.byte_size)
    return response


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_backup(request):
    backup_status = _backup_status_payload()
    if not backup_status["ready"]:
        return Response(
            {
                "detail": "Backup sources are incomplete.",
                "backup_status": backup_status,
            },
            status=status.HTTP_409_CONFLICT,
        )

    target_path_raw = str(request.data.get("target_path", "") or "").strip()
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
        for entry in backup_status["source_roots"]:
            source_path = Path(entry["path"])
            destination = backup_root / entry["label"]
            copied_count = _copy_backup_source_tree(source_path, destination)
            copied_roots.append(
                {
                    "label": entry["label"],
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


@api_view(["GET", "POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_network_nodes(request):
    if request.method == "GET":
        nodes = NetworkNode.objects.select_related("owning_center").order_by(
            "display_name",
            "pk",
        )
        return Response(
            [_network_node_payload(node) for node in nodes],
            status=status.HTTP_200_OK,
        )

    data = request.data
    display_name = str(data.get("display_name", "") or "").strip()
    role = str(data.get("role", "") or NetworkNode.Role.SITE_NODE).strip()
    base_url = str(data.get("base_url", "") or "").strip()
    provided_node_key = str(data.get("node_key", "") or "").strip()
    shared_secret = data.get("shared_secret")
    is_active = data.get("is_active", True)

    errors: dict[str, str] = {}
    if not display_name:
        errors["display_name"] = "display_name is required."
    if role not in NetworkNode.Role.values:
        errors["role"] = "Invalid role."
    if not isinstance(is_active, bool):
        errors["is_active"] = "is_active must be a boolean."

    owning_center = _resolve_center_from_payload(data, errors=errors)
    if shared_secret is not None and not isinstance(shared_secret, str):
        errors["shared_secret"] = "shared_secret must be a string."

    if provided_node_key:
        if NetworkNode.objects.filter(node_key=provided_node_key).exists():
            errors["node_key"] = "node_key already exists."

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    node = NetworkNode(
        display_name=display_name,
        role=role,
        base_url=base_url,
        is_active=is_active,
        owning_center=owning_center if isinstance(owning_center, Center) else None,
    )
    if provided_node_key:
        node.node_key = provided_node_key
    if isinstance(shared_secret, str) and shared_secret.strip():
        node.set_shared_secret(shared_secret)
    node.save()
    node.refresh_from_db()
    return Response(_network_node_payload(node), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_network_node_detail(request, pk: int):
    node = NetworkNode.objects.select_related("owning_center").filter(pk=pk).first()
    if node is None:
        return Response(
            {"detail": "Network node not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        return Response(_network_node_payload(node), status=status.HTTP_200_OK)

    if request.method == "DELETE":
        node.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    data = request.data
    errors: dict[str, str] = {}

    if "node_key" in data:
        requested_node_key = str(data.get("node_key", "") or "").strip()
        if requested_node_key and requested_node_key != node.node_key:
            errors["node_key"] = "node_key is immutable once assigned."

    if "display_name" in data:
        display_name = str(data.get("display_name", "") or "").strip()
        if not display_name:
            errors["display_name"] = "display_name must not be blank."
        else:
            node.display_name = display_name

    if "role" in data:
        role = str(data.get("role", "") or "").strip()
        if role not in NetworkNode.Role.values:
            errors["role"] = "Invalid role."
        else:
            node.role = role

    if "base_url" in data:
        node.base_url = str(data.get("base_url", "") or "").strip()

    if "is_active" in data:
        is_active = data.get("is_active")
        if not isinstance(is_active, bool):
            errors["is_active"] = "is_active must be a boolean."
        else:
            node.is_active = is_active

    owning_center = _resolve_center_from_payload(data, errors=errors)
    if isinstance(owning_center, Center) or owning_center is None:
        if "owning_center_id" in data or "owning_center_key" in data:
            node.owning_center = owning_center

    if "shared_secret" in data:
        shared_secret = data.get("shared_secret")
        if not isinstance(shared_secret, str):
            errors["shared_secret"] = "shared_secret must be a string."
        elif shared_secret.strip():
            node.set_shared_secret(shared_secret)

    if data.get("clear_shared_secret") is True:
        node.shared_secret_hash = ""
    elif "clear_shared_secret" in data and data.get("clear_shared_secret") is not False:
        errors["clear_shared_secret"] = "clear_shared_secret must be a boolean."

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    node.save()
    node.refresh_from_db()
    return Response(_network_node_payload(node), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_network_node_roles_dropdown(request):
    return Response(_network_node_roles_payload(), status=status.HTTP_200_OK)


__all__ = [
    "application_settings_detail",
    "application_settings_centers_dropdown",
    "application_settings_processors_dropdown",
    "application_settings_annotators_dropdown",
    "application_settings_report_templates_dropdown",
    "application_settings_ai_datasets_dropdown",
    "application_settings_ai_dataset_frame_bucket_distribution",
    "application_settings_ai_dataset_training_manifest",
    "application_settings_model_training_options",
    "application_settings_model_training_runs",
    "application_settings_model_training_run_detail",
    "application_settings_video_dimension_backfill_runs",
    "application_settings_video_dimension_backfill_run_detail",
    "application_settings_ai_dataset_export",
    "application_settings_backup",
    "application_settings_network_nodes",
    "application_settings_network_node_detail",
    "application_settings_network_node_roles_dropdown",
]
