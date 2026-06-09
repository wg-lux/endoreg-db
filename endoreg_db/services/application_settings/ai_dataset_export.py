from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from django.utils import timezone

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.aidataset.aidataset import AIDataSet, AIDataSetExportArtifact
from endoreg_db.services.hub import (
    local_study_server_mode_enabled,
    resolve_allowed_center_id,
)
from endoreg_db.utils.filesystem import paths as path_settings
from endoreg_db.utils.defaults.set_default_center import get_application_settings
from endoreg_db.utils.filesystem.file_operations import atomic_write_file, sha256_file


@dataclass(frozen=True, slots=True)
class ServiceResponse:
    payload: dict[str, Any]
    status_code: int
    artifact: AIDataSetExportArtifact | None = None


@dataclass(frozen=True, slots=True)
class DownloadResponse:
    status_code: int
    payload: dict[str, Any] | None = None
    artifact: AIDataSetExportArtifact | None = None
    file_path: Path | None = None
    filename: str = ""
    sha256: str = ""
    byte_size: int = 0
    content_type: str = "application/json"

    @property
    def is_file_response(self) -> bool:
        return self.file_path is not None


def _normalize_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    return {str(key): value for key, value in payload.items()}


def _integer_param_error_payload(field_name: str) -> dict[str, Any]:
    return {"errors": {field_name: f"{field_name} must be an integer."}}


def _parse_optional_integer_param(
    raw_value: object,
    *,
    field_name: str,
) -> tuple[int | None, ServiceResponse | None]:
    if raw_value in (None, ""):
        return None, None
    if isinstance(raw_value, bool) or not isinstance(
        raw_value, (str, bytes, bytearray, int)
    ):
        return None, ServiceResponse(
            payload=_integer_param_error_payload(field_name),
            status_code=400,
        )
    try:
        return int(raw_value), None
    except (TypeError, ValueError):
        return None, ServiceResponse(
            payload=_integer_param_error_payload(field_name),
            status_code=400,
        )


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


def sanitize_export_token(value: str) -> str:
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


def ai_dataset_export_download_url(artifact: AIDataSetExportArtifact) -> str:
    return (
        f"/api/settings/application/ai_dataset_export/{artifact.artifact_key}/download/"
    )


def ai_dataset_export_payload(artifact: AIDataSetExportArtifact) -> dict[str, Any]:
    return {
        "success": artifact.status == AIDataSetExportArtifact.STATUS_COMPLETED,
        "artifact_id": artifact.artifact_key,
        "dataset_id": artifact.dataset_id,
        "dataset_name": artifact.dataset_name,
        "dataset_type": artifact.dataset_type,
        "output_path": artifact.output_path,
        "download_url": (
            ai_dataset_export_download_url(artifact)
            if artifact.status == AIDataSetExportArtifact.STATUS_COMPLETED
            else None
        ),
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "summary": artifact.summary,
        "status": artifact.status,
        "error": artifact.error or None,
    }


def _resolve_ai_dataset_export_dataset(
    payload: Mapping[str, Any],
) -> tuple[AIDataSet | None, ServiceResponse | None]:
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
            return None, ServiceResponse(
                payload={"errors": {"dataset_id": "AIDataSet not found."}},
                status_code=404,
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
        return None, ServiceResponse(payload={"errors": errors}, status_code=400)

    matches = list(
        AIDataSet.objects.filter(
            name=dataset_name.strip(),
            dataset_type=dataset_type,
        ).order_by("-updated_at", "-pk")[:2]
    )
    if not matches:
        return None, ServiceResponse(
            payload={
                "errors": {
                    "ai_dataset_name": (
                        f"No AIDataSet found for name='{dataset_name.strip()}' "
                        f"and dataset_type='{dataset_type}'."
                    )
                }
            },
            status_code=404,
        )
    if len(matches) > 1:
        return None, ServiceResponse(
            payload={
                "errors": {
                    "ai_dataset_name": (
                        "Multiple AIDataSet rows match this name/type. "
                        "Export by dataset_id to avoid selecting the wrong dataset."
                    )
                }
            },
            status_code=409,
        )
    return matches[0], None


def _dataset_export_scope_error(
    user: Any,
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


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")


def _export_file_name(dataset: AIDataSet, artifact: AIDataSetExportArtifact) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        f"{sanitize_export_token(dataset.name or 'dataset')}"
        f"_{sanitize_export_token(dataset.dataset_type)}"
        f"_{timestamp}_{artifact.artifact_key}.json"
    )


def _mark_artifact_failed(
    artifact: AIDataSetExportArtifact,
    error: str,
) -> AIDataSetExportArtifact:
    artifact.status = AIDataSetExportArtifact.STATUS_FAILED
    artifact.error = error
    artifact.finished_at = timezone.now()
    artifact.save(update_fields=["status", "error", "finished_at", "updated_at"])
    return artifact


def _resolve_export_root(export_root: Path | None) -> Path:
    if export_root is not None:
        return Path(export_root)
    return Path(path_settings.EXPORT_DIR)


def create_ai_dataset_export(
    payload: Mapping[str, Any] | None,
    *,
    user: Any,
    export_root: Path | None = None,
) -> ServiceResponse:
    request_payload = _normalize_payload(payload)
    dataset, dataset_error = _resolve_ai_dataset_export_dataset(request_payload)
    if dataset_error is not None:
        return dataset_error
    assert dataset is not None

    center_key = str(request_payload.get("center_key") or "").strip() or None
    all_centers = _payload_bool(request_payload.get("all_centers"), default=False)
    only_validated = _payload_bool(request_payload.get("only_validated"), default=True)
    scope_error = _dataset_export_scope_error(
        user,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
    )
    if scope_error is not None:
        error_message, status_code = scope_error
        return ServiceResponse(
            payload={"success": False, "error": error_message},
            status_code=status_code,
        )

    artifact = AIDataSetExportArtifact.objects.create(
        dataset=dataset,
        dataset_name=dataset.name,
        dataset_type=dataset.dataset_type,
        ai_model_type=dataset.ai_model_type,
        request_payload=request_payload,
        center_key=center_key,
        all_centers=all_centers,
        only_validated=only_validated,
        status=AIDataSetExportArtifact.STATUS_RUNNING,
    )

    export_dir = _resolve_export_root(export_root) / "ai_datasets"
    file_name = _export_file_name(dataset, artifact)
    output_path = export_dir / file_name

    try:
        export_payload = dataset.export_to_standardized_structure(
            center_key=center_key,
            all_centers=all_centers,
            only_validated=only_validated,
        )
        json_bytes = _json_bytes(export_payload)
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
        _mark_artifact_failed(artifact, str(exc))
        return ServiceResponse(
            payload=ai_dataset_export_payload(artifact),
            status_code=500,
            artifact=artifact,
        )

    return ServiceResponse(
        payload=ai_dataset_export_payload(artifact),
        status_code=201,
        artifact=artifact,
    )


def _coerce_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def prepare_ai_dataset_export_download(
    artifact_id: str,
    *,
    export_root: Path | None = None,
) -> DownloadResponse:
    artifact_uuid = _coerce_uuid(artifact_id)
    artifact = (
        AIDataSetExportArtifact.objects.filter(artifact_id=artifact_uuid).first()
        if artifact_uuid is not None
        else None
    )
    if artifact is None:
        return DownloadResponse(
            payload={"detail": "AI dataset export artifact not found."},
            status_code=404,
        )
    if artifact.status != AIDataSetExportArtifact.STATUS_COMPLETED:
        return DownloadResponse(
            payload=ai_dataset_export_payload(artifact),
            status_code=409,
            artifact=artifact,
        )

    output_path = Path(artifact.output_path)
    resolved_export_root = _resolve_export_root(export_root)
    try:
        output_path.resolve().relative_to(resolved_export_root.resolve())
    except ValueError:
        _mark_artifact_failed(
            artifact,
            "Export artifact path is outside the configured export root.",
        )
        return DownloadResponse(
            payload=ai_dataset_export_payload(artifact),
            status_code=500,
            artifact=artifact,
        )

    if not output_path.is_file():
        _mark_artifact_failed(artifact, "Export artifact file is missing from disk.")
        return DownloadResponse(
            payload=ai_dataset_export_payload(artifact),
            status_code=410,
            artifact=artifact,
        )

    return DownloadResponse(
        status_code=200,
        artifact=artifact,
        file_path=output_path,
        filename=artifact.download_filename or output_path.name,
        sha256=artifact.sha256,
        byte_size=artifact.byte_size,
    )
