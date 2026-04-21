from __future__ import annotations

import json
import shutil
import threading
import traceback
from collections import Counter
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from django.core.management import call_command
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.models import (
    AIDataSet,
    Center,
    EndoscopyProcessor,
    ImageClassificationAnnotation,
    NetworkNode,
    PatientExaminationReport,
)
from endoreg_db.services.hub import deployment_profile_payload
from endoreg_db.utils.ai.model_training.config import (
    DEFAULT_LABELSET_VERSION_TO_TRAIN,
)
from endoreg_db.utils.file_operations import atomic_write_file
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

_MODEL_TRAINING_RUNS: dict[str, dict[str, Any]] = {}
_MODEL_TRAINING_RUNS_LOCK = threading.Lock()


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
        "updated_at": settings_obj.updated_at.isoformat()
        if settings_obj.updated_at
        else None,
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


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _store_model_training_run(run_id: str, **updates: object) -> dict[str, Any]:
    with _MODEL_TRAINING_RUNS_LOCK:
        current = _MODEL_TRAINING_RUNS.setdefault(run_id, {})
        current.update(updates)
        return dict(current)


def _get_model_training_run(run_id: str) -> dict[str, Any] | None:
    with _MODEL_TRAINING_RUNS_LOCK:
        run = _MODEL_TRAINING_RUNS.get(run_id)
        return dict(run) if run is not None else None


def _model_training_run_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "dataset_id": run["dataset_id"],
        "dataset_name": run.get("dataset_name"),
        "backbone_name": run["backbone_name"],
        "feature_mode": run["feature_mode"],
        "freeze_backbone": run["freeze_backbone"],
        "epochs": run["epochs"],
        "batch_size": run["batch_size"],
        "labelset_version": run["labelset_version"],
        "treat_unlabeled_as_negative": run["treat_unlabeled_as_negative"],
        "backbone_checkpoint": run.get("backbone_checkpoint"),
        "created_at": run["created_at"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "result": run.get("result"),
        "error": run.get("error"),
        "stdout": run.get("stdout", ""),
    }


def _execute_model_training_run(
    run_id: str,
    *,
    command_kwargs: dict[str, Any],
) -> None:
    _store_model_training_run(run_id, status="running", started_at=_utcnow_iso())
    stdout = StringIO()
    stderr = StringIO()
    try:
        result = call_command(
            "train_image_multilabel_model",
            stdout=stdout,
            stderr=stderr,
            **command_kwargs,
        )
        output = stdout.getvalue()
        error_output = stderr.getvalue()
        if error_output:
            output = f"{output}\n{error_output}".strip()
        _store_model_training_run(
            run_id,
            status="completed",
            finished_at=_utcnow_iso(),
            stdout=output,
            result=result,
            error=None,
        )
    except Exception as exc:
        output = stdout.getvalue()
        error_output = stderr.getvalue()
        trace = traceback.format_exc()
        combined_output = "\n".join(
            chunk for chunk in (output, error_output, trace) if chunk
        ).strip()
        _store_model_training_run(
            run_id,
            status="failed",
            finished_at=_utcnow_iso(),
            stdout=combined_output,
            error=str(exc),
            result=None,
        )


def _launch_model_training_run(run_id: str, *, command_kwargs: dict[str, Any]) -> None:
    thread = threading.Thread(
        target=_execute_model_training_run,
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
        "owning_center_key": owning_center.center_key
        if owning_center is not None
        else None,
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
        processor=processor_value
        if ("processor_id" in data or "processor_name" in data)
        else None,
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
def application_settings_model_training_options(request):
    return Response(
        {
            "ai_datasets": [
                entry
                for entry in _application_settings_ai_dataset_entries()
                if entry["dataset_type"] == AIDataSet.DATASET_TYPE_IMAGE
                and entry["ai_model_type"] == AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL
            ],
            "backbones": list(MODEL_TRAINING_BACKBONE_OPTIONS),
            "feature_modes": list(MODEL_TRAINING_FEATURE_MODE_OPTIONS),
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


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_runs(request):
    payload: dict[str, Any] = request.data or {}

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
    run_id = uuid4().hex
    command_kwargs = {
        "dataset_id": dataset.pk,
        "backbone_name": backbone_name,
        "backbone_checkpoint": backbone_checkpoint,
        "epochs": epochs,
        "batch_size": batch_size,
        "labelset_version": labelset_version,
        "freeze_backbone": freeze_backbone,
        "treat_unlabeled_as_negative": treat_unlabeled_as_negative,
    }
    run = _store_model_training_run(
        run_id,
        status="queued",
        dataset_id=dataset.pk,
        dataset_name=dataset.name,
        backbone_name=backbone_name,
        feature_mode=feature_mode,
        freeze_backbone=freeze_backbone,
        epochs=epochs,
        batch_size=batch_size,
        labelset_version=labelset_version,
        treat_unlabeled_as_negative=treat_unlabeled_as_negative,
        backbone_checkpoint=backbone_checkpoint,
        created_at=_utcnow_iso(),
        started_at=None,
        finished_at=None,
        result=None,
        error=None,
        stdout="",
    )
    _launch_model_training_run(run_id, command_kwargs=command_kwargs)
    return Response(_model_training_run_payload(run), status=status.HTTP_202_ACCEPTED)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_model_training_run_detail(request, run_id: str):
    run = _get_model_training_run(run_id)
    if run is None:
        return Response(
            {"detail": "Training run not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(_model_training_run_payload(run), status=status.HTTP_200_OK)


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


@api_view(["POST"])
@permission_classes([EnvironmentAwarePermission])
def application_settings_ai_dataset_export(request):
    payload: dict[str, Any] = request.data or {}
    settings_obj = get_application_settings()

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
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    dataset = (
        AIDataSet.objects.filter(name=dataset_name.strip(), dataset_type=dataset_type)
        .order_by("-updated_at", "-pk")
        .first()
    )
    if dataset is None:
        return Response(
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

    export_dir = EXPORT_DIR / "ai_datasets"
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    file_name = (
        f"{_sanitize_export_token(dataset.name or 'dataset')}"
        f"_{_sanitize_export_token(dataset.dataset_type)}"
        f"_{timestamp}.json"
    )
    output_path = export_dir / file_name

    export_payload = dataset.export_to_standardized_structure()
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

    return Response(
        {
            "success": True,
            "dataset_id": dataset.pk,
            "dataset_name": dataset.name,
            "dataset_type": dataset.dataset_type,
            "output_path": str(output_path),
            "summary": export_payload.get("summary", {}),
        },
        status=status.HTTP_201_CREATED,
    )


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
        backup_root.mkdir(parents=True, exist_ok=False)

        copied_roots: list[dict[str, Any]] = []
        for entry in backup_status["source_roots"]:
            source_path = Path(entry["path"])
            destination = backup_root / entry["label"]
            shutil.copytree(source_path, destination)
            copied_roots.append(
                {
                    "label": entry["label"],
                    "source_path": str(source_path),
                    "destination_path": str(destination),
                    "file_count": entry["file_count"],
                }
            )

        manifest = {
            "created_at": datetime.now().isoformat(),
            "target_root": str(backup_root),
            "copied_roots": copied_roots,
        }
        (backup_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
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
    "application_settings_model_training_options",
    "application_settings_model_training_runs",
    "application_settings_model_training_run_detail",
    "application_settings_ai_dataset_export",
    "application_settings_backup",
    "application_settings_network_nodes",
    "application_settings_network_node_detail",
    "application_settings_network_node_roles_dropdown",
]
