# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, Protocol, TypedDict, cast
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import OperationalError, ProgrammingError, connection
from django.db.models.fields.files import FieldFile

from endoreg_db.management.commands._profiling import (
    CommandProfilingConfig,
    add_profiling_arguments,
    command_profiling_config_from_options,
    profiling_metadata,
    run_with_optional_profile,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.services.hub.deployment import local_study_server_mode_enabled
from endoreg_db.utils.file_operations import (
    atomic_handoff_file,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.paths import EndoregPathsModel

_CHUNK_SIZE = 1024 * 1024


class _KcacheUploadProvenance(TypedDict):
    file_type: str
    prediction_model_name: str | None
    processor_name: str
    ingest_variant: str


class _IngestModule(Protocol):
    def resolve_default_center(self) -> Center | None: ...

    def _default_processor_name(self) -> str | None: ...

    def create_or_reuse_watcher_upload_job(
        self,
        *,
        file_path: Path,
        content_type: str,
        source_center: Center | None = None,
        source_system: str = "watcher",
        storage_class: str = UploadJob.StorageClass.INGEST,
        storage_tier: str = UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy: str = UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        processing_provenance: _KcacheUploadProvenance | None = None,
    ) -> tuple[UploadJob, bool]: ...

    def _run_watcher_upload_job_inline(
        self,
        *,
        upload_job: UploadJob,
        watched_path: Path,
        normalized_type: str,
        source_center: Center,
        processor_name: str | None = None,
    ) -> UploadJob: ...


@dataclass(frozen=True)
class _ImportRequest:
    source_path: Path
    apply_changes: bool
    center: Center | None
    processor: EndoscopyProcessor | None
    center_name_option: str | None
    processor_name_option: str | None
    source_system: str
    prediction_model_name: str | None
    watched_path: Path
    source_size: int
    source_sha256: str
    json_output: bool


@dataclass(frozen=True)
class _ApplyRequest:
    request: _ImportRequest
    center: Center
    processor_name: str


def _prepare_import_request(options: dict[str, object]) -> _ImportRequest:
    source_path = _source_path_from_options(options)
    apply_changes = bool(options.get("apply"))
    center_name_option = _optional_str(options.get("center_name"))
    processor_name_option = _optional_str(options.get("processor_name"))
    if local_study_server_mode_enabled():
        raise CommandError(
            "Raw watcher video ingestion is disabled for local_study_server; "
            "use the preanonymized ingest path instead."
        )
    center = (
        _resolve_center(center_name_option)
        if apply_changes or center_name_option is not None
        else None
    )
    processor = (
        _resolve_processor(processor_name_option)
        if apply_changes or processor_name_option is not None
        else None
    )
    drop_dir = EndoregPathsModel.from_environment().watcher_video_drop
    drop_name = _resolve_drop_name(
        source_path=source_path,
        requested_drop_name=_optional_str(options.get("drop_name")),
    )
    return _ImportRequest(
        source_path=source_path,
        apply_changes=apply_changes,
        center=center,
        processor=processor,
        center_name_option=center_name_option,
        processor_name_option=processor_name_option,
        source_system=_required_str(options.get("source_system"), "--source-system"),
        prediction_model_name=_optional_str(options.get("prediction_model_name")),
        watched_path=drop_dir / drop_name,
        source_size=source_path.stat().st_size,
        source_sha256=sha256_file(source_path),
        json_output=bool(options.get("json_output")),
    )


def _initial_import_payload(
    request: _ImportRequest,
    profiling_config: CommandProfilingConfig,
) -> dict[str, Any]:
    return {
        "apply": request.apply_changes,
        "source_path": str(request.source_path),
        "source_size_bytes": request.source_size,
        "source_sha256": request.source_sha256,
        "center_name": (
            _required_model_str(request.center, "name")
            if request.center is not None
            else request.center_name_option
        ),
        "processor_name": (
            _required_model_str(request.processor, "name")
            if request.processor is not None
            else request.processor_name_option
        ),
        "source_system": request.source_system,
        "watched_path": str(request.watched_path),
        "prediction_model_name": request.prediction_model_name,
        **_database_context_payload(),
        **profiling_metadata(profiling_config),
    }


def _require_apply_request(request: _ImportRequest) -> _ApplyRequest:
    if request.center is None:
        raise CommandError("No center is configured for watcher ingestion.")
    if request.processor is None:
        raise CommandError("No EndoscopyProcessor is configured for video ingestion.")
    if request.watched_path.exists():
        raise CommandError(
            f"Watcher drop target already exists: {request.watched_path}"
        )
    if request.source_path.resolve() == request.watched_path.resolve(strict=False):
        raise CommandError(
            "source_path resolves to the watcher target; choose a different "
            "--drop-name or source file."
        )
    return _ApplyRequest(
        request=request,
        center=request.center,
        processor_name=_required_model_str(request.processor, "name"),
    )


def _create_upload_job(
    apply_request: _ApplyRequest,
) -> tuple[_IngestModule, UploadJob, bool]:
    request = apply_request.request
    atomic_handoff_file(
        destination=request.watched_path,
        content=_iter_file_chunks(request.source_path),
        required_bytes=request.source_size,
    )
    ingest = _load_ingest()
    upload_job, created = ingest.create_or_reuse_watcher_upload_job(
        file_path=request.watched_path,
        content_type="video/mp4",
        source_center=apply_request.center,
        source_system=request.source_system,
        storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
        retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        processing_provenance={
            "file_type": "video",
            "prediction_model_name": request.prediction_model_name,
            "processor_name": apply_request.processor_name,
            "ingest_variant": "kcache_video_import",
        },
    )
    return ingest, upload_job, created


def _run_or_cleanup_upload_job(
    *,
    ingest: _IngestModule,
    upload_job: UploadJob,
    created: bool,
    apply_request: _ApplyRequest,
) -> tuple[UploadJob, bool, bool]:
    reused_for_inline = not created and _should_retry_command_owned_upload_job(
        upload_job
    )
    if created or reused_for_inline:
        upload_job = ingest._run_watcher_upload_job_inline(
            upload_job=upload_job,
            watched_path=apply_request.request.watched_path,
            normalized_type="video",
            source_center=apply_request.center,
            processor_name=apply_request.processor_name,
        )
        return upload_job, True, reused_for_inline
    safe_unlink_file(apply_request.request.watched_path, missing_ok=True)
    return upload_job, False, reused_for_inline


def _update_job_payload(
    payload: dict[str, Any],
    *,
    upload_job: UploadJob,
    created: bool,
    inline_ingest_ran: bool,
    reused_for_inline: bool,
    watched_path: Path,
) -> str:
    upload_job.refresh_from_db()
    status = _required_model_str(upload_job, "status")
    payload.update(
        {
            "upload_job_id": str(_required_model_value(upload_job, "id")),
            "upload_job_created": created,
            "content_hash": _required_model_str(upload_job, "content_hash"),
            "status": status,
            "inline_ingest_ran": inline_ingest_ran,
            "upload_job_reused_for_inline": reused_for_inline,
            "watched_path_exists": watched_path.exists(),
            "cleanup_status": _required_model_str(upload_job, "cleanup_status"),
            "processing_provenance": _required_model_dict(
                upload_job,
                "processing_provenance",
            ),
            "video": _video_payload_for_upload_job(upload_job),
        }
    )
    return status


def _raise_for_failed_upload_job(upload_job: UploadJob, status: str) -> None:
    error_detail = _optional_str(getattr(upload_job, "error_detail", None))
    if status == UploadJob.Status.ERROR.value:
        raise CommandError(error_detail or "Watcher import failed.")
    if status == UploadJob.Status.LOST.value:
        raise CommandError(error_detail or "Watcher import was lost.")


class Command(BaseCommand):
    requires_system_checks = cast(Any, ())
    help = (
        "Run a foreground watcher-style video import for cProfile/KCachegrind. "
        "The command atomically drops a source file into the watcher video intake, "
        "creates the watcher UploadJob, and runs the inline video import path."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "source_path",
            help="Existing video file to hand off through the watcher ingest path.",
        )
        parser.add_argument(
            "--center-name",
            default=None,
            help="Center name for ingest. Defaults to the configured watcher center.",
        )
        parser.add_argument(
            "--processor-name",
            default=None,
            help=(
                "EndoscopyProcessor name for video import. Defaults to the "
                "configured watcher processor fallback."
            ),
        )
        parser.add_argument(
            "--prediction-model-name",
            default=None,
            help="Optional AiModel name to record for post-import prediction dispatch.",
        )
        parser.add_argument(
            "--source-system",
            default="watcher",
            help="Source system recorded on the watcher UploadJob.",
        )
        parser.add_argument(
            "--drop-name",
            default=None,
            help=(
                "Filename to use inside the watcher video drop directory. Defaults "
                "to a unique kcache-* name preserving the source suffix."
            ),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually create the watcher handoff and run inline ingest.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Emit machine-readable JSON.",
        )
        add_profiling_arguments(parser)

    def handle(self, *args: object, **options: object) -> None:
        profiling_config = command_profiling_config_from_options(options)
        return run_with_optional_profile(
            lambda: self._handle_unprofiled(*args, **options),
            config=profiling_config,
        )

    def _handle_unprofiled(self, *args: object, **options: object) -> None:
        _ = args
        profiling_config = command_profiling_config_from_options(options)
        request = _prepare_import_request(options)
        payload = _initial_import_payload(request, profiling_config)
        if not request.apply_changes:
            payload["status"] = "would_ingest"
            self._write_payload(payload, json_output=request.json_output)
            return
        apply_request = _require_apply_request(request)
        ingest, upload_job, created = _create_upload_job(apply_request)
        upload_job, inline_ingest_ran, reused_for_inline = _run_or_cleanup_upload_job(
            ingest=ingest,
            upload_job=upload_job,
            created=created,
            apply_request=apply_request,
        )
        upload_job_status = _update_job_payload(
            payload,
            upload_job=upload_job,
            created=created,
            inline_ingest_ran=inline_ingest_ran,
            reused_for_inline=reused_for_inline,
            watched_path=request.watched_path,
        )
        self._write_payload(payload, json_output=request.json_output)
        _raise_for_failed_upload_job(upload_job, upload_job_status)

    def _write_payload(self, payload: dict[str, Any], *, json_output: bool) -> None:
        if json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        status = str(payload.get("status") or "unknown")
        upload_job_id = str(payload.get("upload_job_id") or "")
        line = (
            f"status={status} center={payload['center_name']} "
            f"processor={payload['processor_name']} "
            f"watched_path={payload['watched_path']}"
        )
        if upload_job_id:
            line = (
                f"{line} upload_job={upload_job_id} "
                f"inline_ingest_ran={payload.get('inline_ingest_ran')}"
            )
        if status == UploadJob.Status.ANONYMIZED.value:
            self.stdout.write(self.style.SUCCESS(line))
        elif status in {UploadJob.Status.ERROR.value, UploadJob.Status.LOST.value}:
            self.stderr.write(self.style.ERROR(line))
        else:
            self.stdout.write(line)


def _source_path_from_options(options: dict[str, object]) -> Path:
    source_path = Path(_required_str(options.get("source_path"), "source_path"))
    if not source_path.exists():
        raise CommandError(f"Source file does not exist: {source_path}")
    if not source_path.is_file():
        raise CommandError(f"Source path is not a file: {source_path}")
    if not source_path.suffix:
        raise CommandError("Source file must have a video filename suffix.")
    return source_path


def _resolve_center(center_name: str | None) -> Center:
    try:
        if center_name is not None:
            center = Center.objects.filter(name=center_name).first()
            if center is None:
                raise CommandError(f"Center not found: {center_name}")
            return center

        ingest = _load_ingest()
        center = ingest.resolve_default_center()
    except (OperationalError, ProgrammingError) as exc:
        _raise_database_schema_error(exc)
    if center is None:
        raise CommandError("No center is configured for watcher ingestion.")
    return center


def _resolve_processor(processor_name: str | None) -> EndoscopyProcessor:
    try:
        if processor_name is not None:
            processor = EndoscopyProcessor.objects.filter(name=processor_name).first()
            if processor is None:
                raise CommandError(f"EndoscopyProcessor not found: {processor_name}")
            return processor

        ingest = _load_ingest()
        default_processor_name = ingest._default_processor_name()
        processor = (
            EndoscopyProcessor.objects.filter(name=default_processor_name).first()
            if default_processor_name is not None
            else None
        )
    except (OperationalError, ProgrammingError) as exc:
        _raise_database_schema_error(exc)
    if processor is None:
        raise CommandError("No EndoscopyProcessor is configured for video ingestion.")
    return processor


def _load_ingest() -> _IngestModule:
    from endoreg_db.services.hub import ingest

    return cast(_IngestModule, ingest)


def _database_context_payload() -> dict[str, str]:
    return {
        "django_settings_module": str(settings.SETTINGS_MODULE),
        "database_name": str(connection.settings_dict.get("NAME", "")),
    }


def _raise_database_schema_error(exc: Exception) -> NoReturn:
    context = _database_context_payload()
    raise CommandError(
        "Database schema is not ready for kcache_video_import. "
        f"django_settings_module={context['django_settings_module']} "
        f"database={context['database_name']}. "
        "Run `python manage.py migrate` with the same test DB settings, then "
        "`python manage.py load_base_db_data` if center/processor rows are missing."
    ) from exc


def _resolve_drop_name(
    *,
    source_path: Path,
    requested_drop_name: str | None,
) -> str:
    if requested_drop_name is None:
        suffix = source_path.suffix.lower()
        return f"kcache-{source_path.stem}-{uuid4().hex[:12]}{suffix}"

    drop_name = requested_drop_name.strip()
    if not drop_name:
        raise CommandError("--drop-name must not be empty.")
    if Path(drop_name).name != drop_name or "/" in drop_name or "\\" in drop_name:
        raise CommandError("--drop-name must be a filename, not a path.")
    if not Path(drop_name).suffix:
        raise CommandError("--drop-name must include a video filename suffix.")
    return drop_name


def _iter_file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            yield chunk


def video_payload_for_upload_job(upload_job: UploadJob) -> dict[str, object] | None:
    return _video_payload_for_upload_job(upload_job)


def _video_payload_for_upload_job(upload_job: UploadJob) -> dict[str, object] | None:
    content_hash = _required_model_str(upload_job, "content_hash")
    video = (
        VideoFile.objects.select_related("state")
        .filter(video_hash=content_hash)
        .first()
    )
    if video is None:
        return None

    state = getattr(video, "state", None)
    processed_video_hash = _ensure_processed_video_hash(video)
    return {
        "id": int(video.pk),
        "video_hash": _required_model_str(video, "video_hash"),
        "processed_video_hash": processed_video_hash,
        "raw_file": _field_file_name(getattr(video, "raw_file", None)),
        "processed_file": _field_file_name(getattr(video, "processed_file", None)),
        "anonymization_status": _state_choice_value(state, "anonymization_status"),
        "anonymized": _optional_model_bool(state, "anonymized"),
        "anonymization_validated": _optional_model_bool(
            state,
            "anonymization_validated",
        ),
        "sensitive_meta_processed": _optional_model_bool(
            state,
            "sensitive_meta_processed",
        ),
    }


def _ensure_processed_video_hash(video: VideoFile) -> str | None:
    processed_video_hash = _optional_str(getattr(video, "processed_video_hash", None))
    if processed_video_hash is not None:
        return processed_video_hash

    processed_file = getattr(video, "processed_file", None)
    if not _field_file_name(processed_file):
        return None
    if not isinstance(processed_file, FieldFile):
        raise CommandError("Video processed_file is not a Django FieldFile.")

    processed_video_hash = sha256_file(processed_file)
    setattr(video, "processed_video_hash", processed_video_hash)
    video.save(update_fields=["processed_video_hash", "date_modified"])
    return processed_video_hash


def _should_retry_command_owned_upload_job(upload_job: UploadJob) -> bool:
    if _required_model_str(upload_job, "status") == UploadJob.Status.ANONYMIZED.value:
        return False

    provenance = _required_model_dict(upload_job, "processing_provenance")
    return provenance.get("ingest_variant") == "kcache_video_import"


def _required_model_value(instance: object, field_name: str) -> object:
    value = getattr(instance, field_name, None)
    if value is None:
        raise CommandError(f"{type(instance).__name__}.{field_name} must not be empty.")
    return value


def _required_model_str(instance: object, field_name: str) -> str:
    value = _required_model_value(instance, field_name)
    if not isinstance(value, str) or not value.strip():
        raise CommandError(
            f"{type(instance).__name__}.{field_name} must be a non-empty string."
        )
    return value


def _required_model_dict(instance: object, field_name: str) -> dict[str, object]:
    value = _required_model_value(instance, field_name)
    if not isinstance(value, dict):
        raise CommandError(f"{type(instance).__name__}.{field_name} must be an object.")
    return cast(dict[str, object], value)


def _field_file_name(field_file: object) -> str:
    name = getattr(field_file, "name", None)
    return name if isinstance(name, str) else ""


def _state_choice_value(state: object, field_name: str) -> str | None:
    if state is None:
        return None
    choice_value = getattr(getattr(state, field_name, None), "value", None)
    if choice_value is None:
        return None
    if not isinstance(choice_value, str):
        raise CommandError(f"VideoState.{field_name}.value must be a string.")
    return choice_value


def _optional_model_bool(instance: object, field_name: str) -> bool | None:
    if instance is None:
        return None
    value = getattr(instance, field_name, None)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise CommandError(f"{type(instance).__name__}.{field_name} must be boolean.")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_str(value: object, label: str) -> str:
    text = _optional_str(value)
    if text is None:
        raise CommandError(f"{label} must not be empty.")
    return text
