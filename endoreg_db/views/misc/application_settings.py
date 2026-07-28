# pyright: reportPrivateUsage=false, reportUnusedFunction=false
from __future__ import annotations

import threading
import traceback
from collections import Counter
from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Any, Protocol, cast
from uuid import uuid4

from django.db import models
from lx_dtypes.models.contracts.application_settings import (
    ApplicationSettingsPayload,
)
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.services.hub import deployment_profile_payload
from endoreg_db.services.video_dimension_backfill import (
    VideoDimensionBackfillResult,
    backfill_anonymized_video_dimensions,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.set_default_center import (
    get_application_defaults,
    get_application_settings,
    update_application_defaults,
)
from endoreg_db.views.misc.application_settings_backup import (
    _backup_status_payload,
    _required_backup_sources as _backup_required_backup_sources,
    application_settings_backup,
    required_backup_sources as _backup_required_backup_sources_public,
)
from endoreg_db.views.misc.application_settings_ai_datasets import (
    _ai_dataset_name,
    _ai_dataset_type,
    _parse_optional_integer_param,
    application_settings_ai_dataset_attachments,
    application_settings_ai_dataset_export,
    application_settings_ai_dataset_export_download,
    application_settings_ai_dataset_frame_bucket_distribution,
    application_settings_ai_dataset_training_manifest,
    application_settings_ai_datasets_dropdown,
)
from endoreg_db.views.misc.application_settings_model_training import (
    _MODEL_TRAINING_SERVER_INSTANCE_ID,
    _launch_model_training_run,
    _mark_lost_model_training_runs,
    _model_training_run_payload,
    application_settings_model_training_options,
    application_settings_model_training_run_detail,
    application_settings_model_training_runs,
)
from endoreg_db.views.misc.application_settings_network_nodes import (
    application_settings_network_node_detail,
    application_settings_network_node_roles_dropdown,
    application_settings_network_nodes,
)

_VIDEO_DIMENSION_BACKFILL_RUNS: dict[str, dict[str, Any]] = {}
_VIDEO_DIMENSION_BACKFILL_RUNS_LOCK = threading.Lock()

MODEL_TRAINING_SERVER_INSTANCE_ID = _MODEL_TRAINING_SERVER_INSTANCE_ID

launch_model_training_run = _launch_model_training_run
mark_lost_model_training_runs = _mark_lost_model_training_runs
model_training_run_payload = _model_training_run_payload
_required_backup_sources = _backup_required_backup_sources
required_backup_sources = _backup_required_backup_sources_public


class _RequestUserWithUsername(Protocol):
    is_authenticated: bool
    username: str


def _request_user_with_username(request: Request) -> _RequestUserWithUsername:
    return cast(_RequestUserWithUsername, request.user)


def _center_field(value: Center | None) -> Center | None:
    return value


def _model_datetime(value: datetime | None) -> datetime | None:
    return value


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
