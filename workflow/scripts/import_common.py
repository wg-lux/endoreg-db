from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from endoreg_db.utils.file_operations import atomic_write_file

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class RuleResources(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    threads: int = Field(ge=1)
    mem_mb: int = Field(ge=1)


class ImportResources(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    video: RuleResources
    report: RuleResources


class ImportJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source: Path
    center_name: str = Field(min_length=1)
    retry: bool = False


class VideoImportJob(ImportJob):
    processor_name: str = Field(min_length=1)


class ReportImportJob(ImportJob):
    @field_validator("source")
    @classmethod
    def validate_report_source_suffix(cls, source: Path) -> Path:
        if source.suffix.lower() not in {".pdf", ".txt"}:
            raise ValueError("report source must have a .pdf or .txt suffix")
        return source


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    django_settings_module: str | None
    receipt_directory: Path
    resources: ImportResources
    video_imports: dict[str, VideoImportJob]
    report_imports: dict[str, ReportImportJob]

    @field_validator("django_settings_module")
    @classmethod
    def validate_settings_module(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("django_settings_module cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_job_identifiers(self) -> "WorkflowConfig":
        invalid_identifiers = sorted(
            identifier
            for identifier in (*self.video_imports, *self.report_imports)
            if _JOB_ID_PATTERN.fullmatch(identifier) is None
        )
        if invalid_identifiers:
            invalid = ", ".join(invalid_identifiers)
            raise ValueError(f"invalid import job identifiers: {invalid}")
        return self


class ImportReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    job_id: str
    media_type: Literal["video", "report"]
    source_sha256: str
    database_id: int = Field(ge=1)
    content_hash: str = Field(min_length=1)
    retry_requested: bool
    completed_at: datetime


def configure_django(configured_settings_module: str | None) -> None:
    environment_settings_module = os.environ.get("DJANGO_SETTINGS_MODULE")
    if (
        configured_settings_module is not None
        and environment_settings_module is not None
        and environment_settings_module != configured_settings_module
    ):
        raise RuntimeError(
            "Configured django_settings_module conflicts with DJANGO_SETTINGS_MODULE."
        )

    settings_module = configured_settings_module or environment_settings_module
    if settings_module is None:
        raise RuntimeError(
            "Set django_settings_module in config/imports.yaml or export "
            "DJANGO_SETTINGS_MODULE."
        )

    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    import django

    django.setup()


def require_source(expected_source: Path, snakemake_source: str) -> Path:
    source = Path(snakemake_source)
    if source.resolve() != expected_source.resolve():
        raise RuntimeError("Snakemake input does not match the validated job source.")
    if not source.is_file():
        raise FileNotFoundError(f"Import source is not a regular file: {source}")
    return source


def completed_receipt(
    *,
    job_id: str,
    media_type: Literal["video", "report"],
    source_sha256: str,
    database_id: int,
    content_hash: str,
    retry_requested: bool,
) -> ImportReceipt:
    return ImportReceipt(
        job_id=job_id,
        media_type=media_type,
        source_sha256=source_sha256,
        database_id=database_id,
        content_hash=content_hash,
        retry_requested=retry_requested,
        completed_at=datetime.now(timezone.utc),
    )


def write_receipt(receipt: ImportReceipt, destination: Path) -> None:
    payload = f"{receipt.model_dump_json(indent=2)}\n".encode("utf-8")
    atomic_write_file(
        destination=destination,
        content=(payload,),
        required_bytes=len(payload),
        file_mode=0o600,
        dir_mode=0o700,
    )
