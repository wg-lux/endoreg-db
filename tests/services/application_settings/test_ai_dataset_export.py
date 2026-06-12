from __future__ import annotations

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import json
from pathlib import Path
from typing import Protocol, cast
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.test.utils import override_settings

from endoreg_db.models import AIDataSet, AIDataSetExportArtifact
from endoreg_db.services.application_settings.ai_dataset_export import (
    create_ai_dataset_export,
    prepare_ai_dataset_export_download,
    sanitize_export_token,
)
from endoreg_db.utils.file_operations import atomic_write_file, sha256_file


class _UserManager(Protocol):
    def create_user(
        self,
        username: str,
        password: str | None = None,
        **extra_fields: object,
    ) -> AbstractBaseUser: ...


def _dataset() -> AIDataSet:
    return AIDataSet.objects.create(
        name=f"dataset-export-{uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )


@pytest.mark.django_db
def test_create_ai_dataset_export_writes_json_artifact(tmp_path: Path) -> None:
    dataset = _dataset()
    export_payload = {
        "schema_version": "1.0",
        "dataset": {"name": dataset.name},
        "summary": {"image_annotation_count": 0},
    }

    with patch.object(
        AIDataSet,
        "export_to_standardized_structure",
        return_value=export_payload,
    ) as exporter:
        result = create_ai_dataset_export(
            {"dataset_id": dataset.pk},
            user=None,
            export_root=tmp_path,
        )

    assert result.status_code == 201
    payload = result.payload
    assert payload["success"] is True
    assert payload["dataset_id"] == dataset.pk
    assert payload["download_url"].endswith(f"/{payload['artifact_id']}/download/")
    output_path = Path(payload["output_path"])
    assert output_path.is_file()
    assert output_path.parent == tmp_path / "ai_datasets"
    assert json.loads(output_path.read_text(encoding="utf-8")) == export_payload
    assert payload["sha256"] == sha256_file(output_path)
    assert payload["byte_size"] == output_path.stat().st_size
    assert payload["summary"] == export_payload["summary"]
    assert exporter.call_args.kwargs == {
        "center_key": None,
        "all_centers": False,
        "only_validated": True,
    }

    artifact = AIDataSetExportArtifact.objects.get(artifact_id=payload["artifact_id"])
    assert artifact.status == AIDataSetExportArtifact.STATUS_COMPLETED
    assert artifact.request_payload == {"dataset_id": dataset.pk}


@pytest.mark.django_db
def test_create_ai_dataset_export_returns_validation_errors(tmp_path: Path) -> None:
    result = create_ai_dataset_export(
        {"dataset_id": "not-an-int"},
        user=None,
        export_root=tmp_path,
    )

    assert result.status_code == 400
    assert result.payload == {
        "errors": {"dataset_id": "dataset_id must be an integer."}
    }
    assert AIDataSetExportArtifact.objects.count() == 0


@pytest.mark.django_db
def test_create_ai_dataset_export_marks_artifact_failed_on_export_error(
    tmp_path: Path,
) -> None:
    dataset = _dataset()

    with patch.object(
        AIDataSet,
        "export_to_standardized_structure",
        side_effect=RuntimeError("export failed"),
    ):
        result = create_ai_dataset_export(
            {"dataset_id": dataset.pk},
            user=None,
            export_root=tmp_path,
        )

    assert result.status_code == 500
    payload = result.payload
    assert payload["success"] is False
    assert payload["status"] == AIDataSetExportArtifact.STATUS_FAILED
    assert payload["error"] == "export failed"

    artifact = AIDataSetExportArtifact.objects.get(artifact_id=payload["artifact_id"])
    assert artifact.status == AIDataSetExportArtifact.STATUS_FAILED
    assert artifact.error == "export failed"
    assert artifact.finished_at is not None


@pytest.mark.django_db
@override_settings(ENDOREG_DEPLOYMENT_ROLE="local_study_server")
def test_create_ai_dataset_export_enforces_local_scope_before_artifact(
    tmp_path: Path,
) -> None:
    dataset = _dataset()
    user_model = get_user_model()
    user = cast(_UserManager, user_model.objects).create_user(
        username="dataset-export-user",
    )

    result = create_ai_dataset_export(
        {
            "dataset_id": dataset.pk,
            "all_centers": True,
        },
        user=user,
        export_root=tmp_path,
    )

    assert result.status_code == 403
    assert result.payload == {
        "success": False,
        "error": "all_centers export requires staff or superuser privileges.",
    }
    assert AIDataSetExportArtifact.objects.count() == 0


@pytest.mark.django_db
def test_prepare_ai_dataset_export_download_returns_file_metadata(
    tmp_path: Path,
) -> None:
    dataset = _dataset()
    output_path = tmp_path / "ai_datasets" / "export.json"
    content = b'{"summary": {}}\n'
    atomic_write_file(
        destination=output_path,
        content=[content],
        required_bytes=len(content),
    )
    artifact = AIDataSetExportArtifact.objects.create(
        dataset=dataset,
        dataset_name=dataset.name,
        dataset_type=dataset.dataset_type,
        ai_model_type=dataset.ai_model_type,
        status=AIDataSetExportArtifact.STATUS_COMPLETED,
        output_path=str(output_path),
        download_filename="download.json",
        sha256=sha256_file(output_path),
        byte_size=len(content),
    )

    result = prepare_ai_dataset_export_download(
        artifact.artifact_key,
        export_root=tmp_path,
    )

    assert result.status_code == 200
    assert result.is_file_response is True
    assert result.file_path == output_path
    assert result.filename == "download.json"
    assert result.sha256 == artifact.sha256
    assert result.byte_size == len(content)


@pytest.mark.django_db
def test_prepare_ai_dataset_export_download_marks_missing_file_failed(
    tmp_path: Path,
) -> None:
    dataset = _dataset()
    artifact = AIDataSetExportArtifact.objects.create(
        dataset=dataset,
        dataset_name=dataset.name,
        dataset_type=dataset.dataset_type,
        ai_model_type=dataset.ai_model_type,
        status=AIDataSetExportArtifact.STATUS_COMPLETED,
        output_path=str(tmp_path / "ai_datasets" / "missing.json"),
        download_filename="missing.json",
        sha256="0" * 64,
        byte_size=10,
    )

    result = prepare_ai_dataset_export_download(
        artifact.artifact_key,
        export_root=tmp_path,
    )

    assert result.status_code == 410
    assert result.payload is not None
    assert result.payload["status"] == AIDataSetExportArtifact.STATUS_FAILED
    assert result.payload["error"] == "Export artifact file is missing from disk."

    artifact.refresh_from_db()
    assert artifact.status == AIDataSetExportArtifact.STATUS_FAILED
    assert artifact.error == "Export artifact file is missing from disk."


@pytest.mark.django_db
def test_prepare_ai_dataset_export_download_rejects_paths_outside_export_root(
    tmp_path: Path,
) -> None:
    dataset = _dataset()
    export_root = tmp_path / "export_root"
    outside_path = tmp_path / "outside.json"
    artifact = AIDataSetExportArtifact.objects.create(
        dataset=dataset,
        dataset_name=dataset.name,
        dataset_type=dataset.dataset_type,
        ai_model_type=dataset.ai_model_type,
        status=AIDataSetExportArtifact.STATUS_COMPLETED,
        output_path=str(outside_path),
        download_filename="outside.json",
        sha256="0" * 64,
        byte_size=10,
    )

    result = prepare_ai_dataset_export_download(
        artifact.artifact_key,
        export_root=export_root,
    )

    assert result.status_code == 500
    assert result.payload is not None
    assert result.payload["status"] == AIDataSetExportArtifact.STATUS_FAILED
    assert (
        result.payload["error"]
        == "Export artifact path is outside the configured export root."
    )

    artifact.refresh_from_db()
    assert artifact.status == AIDataSetExportArtifact.STATUS_FAILED


def test_sanitize_export_token_is_filesystem_safe() -> None:
    assert sanitize_export_token(" Dataset-01_Raw ") == "dataset-01_raw"
    assert sanitize_export_token("   ") == "dataset"
