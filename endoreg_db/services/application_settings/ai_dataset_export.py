from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.aidataset.aidataset import AIDataSet, AIDataSetExportArtifact
from endoreg_db.schemas.aidataset_export import (
    AIDataSetExportRequestPayload,
    dump_ai_dataset_export_request_payload,
    dump_ai_dataset_export_summary,
    parse_ai_dataset_export_request_payload,
)
from endoreg_db.services.hub import (
    local_study_server_mode_enabled,
)
from endoreg_db.services.center_access import resolve_allowed_center_ids
from endoreg_db.utils import paths as path_settings
from endoreg_db.utils.api_urls import endoreg_api_path
from endoreg_db.utils.set_default_center import get_application_settings
from endoreg_db.utils.file_operations import atomic_write_file, sha256_file


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


def _request_validation_errors(
    exc: PydanticValidationError,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    for item in exc.errors(include_url=False):
        location = item.get("loc", ())
        field_name = str(location[0]) if location else "request_payload"
        message = str(item.get("msg", "Invalid value."))
        if message.startswith("Value error, "):
            message = message.removeprefix("Value error, ")
        if not message.endswith("."):
            message = f"{message}."
        errors[field_name] = message
    return errors


def _selection_validation_errors(
    payload: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Preserve all deterministic selection errors from one request.

    Pydantic stops constructing the typed payload when one field has an
    invalid literal.  The endpoint contract still reports independent name
    and type selection errors together, so validate those raw fields at the
    request boundary as well.
    """
    if payload is None:
        return {}

    errors: dict[str, str] = {}
    if "ai_dataset_name" in payload:
        value = payload["ai_dataset_name"]
        if not isinstance(value, str):
            errors["ai_dataset_name"] = "ai_dataset_name must be a string."
        elif not value.strip():
            errors["ai_dataset_name"] = "ai_dataset_name is required."

    if "ai_dataset_type" in payload:
        value = payload["ai_dataset_type"]
        if not isinstance(value, str) or value not in {
            AIDataSet.DATASET_TYPE_IMAGE,
            AIDataSet.DATASET_TYPE_VIDEO,
        }:
            errors["ai_dataset_type"] = (
                "ai_dataset_type must be one of: image, video."
            )
    return errors


def sanitize_export_token(value: str) -> str:
    normalized: list[str] = []
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
    return endoreg_api_path(
        f"settings/application/ai_dataset_export/{artifact.artifact_key}/download/"
    )


def _model_text(instance: object, field_name: str) -> str:
    return str(getattr(instance, field_name, "") or "")


def _model_int(instance: object, field_name: str) -> int:
    value = getattr(instance, field_name, 0) or 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    raise TypeError(f"{field_name} must be numeric.")


def _dataset_text(dataset: AIDataSet, field_name: str) -> str:
    return _model_text(dataset, field_name)


def _center_id(center: Center) -> int:
    return _model_int(center, "id")


def _artifact_text(artifact: AIDataSetExportArtifact, field_name: str) -> str:
    return _model_text(artifact, field_name)


def _artifact_status(artifact: AIDataSetExportArtifact) -> str:
    return _artifact_text(artifact, "status")


def _artifact_dataset_id(artifact: AIDataSetExportArtifact) -> int | None:
    value = getattr(artifact, "dataset_id", None)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    raise TypeError("dataset_id must be numeric.")


def _artifact_byte_size(artifact: AIDataSetExportArtifact) -> int:
    return _model_int(artifact, "byte_size")


def _artifact_summary(artifact: AIDataSetExportArtifact) -> dict[str, Any]:
    return dump_ai_dataset_export_summary(artifact.summary)


def ai_dataset_export_payload(artifact: AIDataSetExportArtifact) -> dict[str, Any]:
    status = _artifact_status(artifact)
    return {
        "success": status == AIDataSetExportArtifact.STATUS_COMPLETED,
        "artifact_id": artifact.artifact_key,
        "dataset_id": _artifact_dataset_id(artifact),
        "dataset_name": _artifact_text(artifact, "dataset_name"),
        "dataset_type": _artifact_text(artifact, "dataset_type"),
        "output_path": _artifact_text(artifact, "output_path"),
        "download_url": (
            ai_dataset_export_download_url(artifact)
            if status == AIDataSetExportArtifact.STATUS_COMPLETED
            else None
        ),
        "sha256": _artifact_text(artifact, "sha256"),
        "byte_size": _artifact_byte_size(artifact),
        "summary": _artifact_summary(artifact),
        "status": status,
        "error": _artifact_text(artifact, "error") or None,
    }


def _resolve_ai_dataset_export_dataset(
    payload: AIDataSetExportRequestPayload,
) -> tuple[AIDataSet | None, ServiceResponse | None]:
    settings_obj = get_application_settings()
    if payload.dataset_id is not None:
        dataset = AIDataSet.objects.filter(pk=payload.dataset_id).first()
        if dataset is None:
            return None, ServiceResponse(
                payload={"errors": {"dataset_id": "AIDataSet not found."}},
                status_code=404,
            )
        return dataset, None

    dataset_name = payload.ai_dataset_name or settings_obj.ai_dataset_name
    dataset_type = payload.ai_dataset_type or settings_obj.ai_dataset_type

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
        allowed_center_ids = resolve_allowed_center_ids(user)
        if allowed_center_ids == frozenset():
            return "You do not have access to export center data.", 403
        if (
            allowed_center_ids is not None
            and _center_id(center) not in allowed_center_ids
        ):
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
        f"{sanitize_export_token(_dataset_text(dataset, 'name') or 'dataset')}"
        f"_{sanitize_export_token(_dataset_text(dataset, 'dataset_type'))}"
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
    try:
        request_payload = parse_ai_dataset_export_request_payload(payload)
    except PydanticValidationError as exc:
        errors = _request_validation_errors(exc)
        errors.update(_selection_validation_errors(payload))
        return ServiceResponse(
            payload={"errors": errors},
            status_code=400,
        )
    dataset, dataset_error = _resolve_ai_dataset_export_dataset(request_payload)
    if dataset_error is not None:
        return dataset_error
    assert dataset is not None

    center_key = request_payload.center_key
    all_centers = request_payload.all_centers
    only_validated = request_payload.only_validated
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
        dataset_name=_dataset_text(dataset, "name"),
        dataset_type=_dataset_text(dataset, "dataset_type"),
        ai_model_type=_dataset_text(dataset, "ai_model_type"),
        request_payload=dump_ai_dataset_export_request_payload(
            request_payload.model_copy(update={"dataset_id": dataset.pk})
        ),
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
    if _artifact_status(artifact) != AIDataSetExportArtifact.STATUS_COMPLETED:
        return DownloadResponse(
            payload=ai_dataset_export_payload(artifact),
            status_code=409,
            artifact=artifact,
        )

    output_path = Path(_artifact_text(artifact, "output_path"))
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
        filename=_artifact_text(artifact, "download_filename") or output_path.name,
        sha256=_artifact_text(artifact, "sha256"),
        byte_size=_artifact_byte_size(artifact),
    )
