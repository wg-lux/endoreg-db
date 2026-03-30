from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    ImageClassificationAnnotation,
    NetworkNode,
    PatientExaminationReport,
)
from endoreg_db.utils.defaults.set_default_center import (
    get_application_defaults,
    get_application_settings,
    update_application_defaults,
)
from endoreg_db.utils.paths import IO_DIR, STORAGE_DIR
from endoreg_db.utils.permissions import EnvironmentAwarePermission


def _required_backup_sources() -> list[Path]:
    return [STORAGE_DIR, IO_DIR]


def _count_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file())


def _backup_status_payload() -> dict[str, Any]:
    required_sources = [path.resolve() for path in _required_backup_sources()]
    missing_paths = [str(path) for path in required_sources if not path.exists()]
    source_roots = [
        {
            "label": "storage" if path == STORAGE_DIR.resolve() else "io",
            "path": str(path),
            "exists": path.exists(),
            "file_count": _count_files(path) if path.exists() else 0,
        }
        for path in required_sources
    ]
    return {
        "ready": len(missing_paths) == 0,
        "missing_paths": missing_paths,
        "required_path_count": len(required_sources),
        "available_path_count": len(required_sources) - len(missing_paths),
        "source_roots": source_roots,
    }


def _settings_payload() -> dict[str, Any]:
    settings_obj = get_application_settings()
    snapshot = get_application_defaults()
    return {
        "id": settings_obj.pk,
        "center_id": snapshot.center_id,
        "center_name": snapshot.center_name,
        "processor_id": snapshot.processor_id,
        "processor_name": snapshot.processor_name,
        "annotator_name": snapshot.annotator_name,
        "report_template_name": snapshot.report_template_name,
        "updated_at": settings_obj.updated_at.isoformat()
        if settings_obj.updated_at
        else None,
        "backup_status": _backup_status_payload(),
    }


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
        return Response(_settings_payload(), status=status.HTTP_200_OK)

    data = request.data
    center_value = data.get("center_id", data.get("center_name"))
    processor_value = data.get("processor_id", data.get("processor_name"))
    annotator_name = data.get("annotator_name")
    report_template_name = data.get("report_template_name")

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

    if errors:
        return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

    update_application_defaults(
        center=center_value if ("center_id" in data or "center_name" in data) else None,
        processor=processor_value
        if ("processor_id" in data or "processor_name" in data)
        else None,
        annotator_name=annotator_name,
        report_template_name=report_template_name,
    )
    return Response(_settings_payload(), status=status.HTTP_200_OK)


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
    "application_settings_backup",
    "application_settings_network_nodes",
    "application_settings_network_node_detail",
    "application_settings_network_node_roles_dropdown",
]
