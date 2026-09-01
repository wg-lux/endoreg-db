from __future__ import annotations

import mimetypes
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from django.core.files.uploadedfile import UploadedFile
from django.db import DatabaseError
from django.http import Http404
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.request import Request
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from kombu.exceptions import OperationalError as KombuOperationalError


class _MagicModule(Protocol):
    def from_buffer(self, buffer: bytes, *, mime: bool) -> str: ...


# Try to import python-magic, but provide fallback if not available
try:
    import magic as _magic_module

    _magic: _MagicModule | None = cast(_MagicModule, _magic_module)
except ImportError:
    _magic = None

MAGIC_AVAILABLE = _magic is not None

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.serializers.hub import UploadJobStatusSerializer
from lx_dtypes.models.contracts import (
    UploadApiRequestPayload,
    validate_upload_api_request_payload,
)
from endoreg_db.services.hub import ingest
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.utils.permissions import EnvironmentAwarePermission

if TYPE_CHECKING:
    from endoreg_db.services.hub.ingest import CeleryTaskDispatcher, UploadProvenance

JsonScalar: TypeAlias = str | int | float | bool
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
UploadContext: TypeAlias = dict[str, JsonValue]


class UploadCreator(Protocol):
    @property
    def is_authenticated(self) -> bool: ...


class _SerializerDataLike(Protocol):
    @property
    def data(self) -> Mapping[str, JsonValue]: ...


class _UploadJobFactoryWithoutCenter(Protocol):
    def __call__(
        self,
        *,
        uploaded_file: UploadedFile,
        content_type: str,
        created_by: UploadCreator,
        source_system: str,
        content_hash: str,
        idempotency_key: str,
        ingest_mode: str,
        storage_class: str,
        storage_tier: str,
        retention_policy: str,
        source_file_persisted: bool,
        cleanup_status: str,
        processing_provenance: "UploadProvenance",
        allow_completed_reuse_without_media: bool,
    ) -> tuple[UploadJob, bool]: ...


class _UploadJobFactoryWithCenter(Protocol):
    def __call__(
        self,
        *,
        uploaded_file: UploadedFile,
        content_type: str,
        created_by: UploadCreator,
        source_center: Center,
        source_system: str,
        content_hash: str,
        idempotency_key: str,
        ingest_mode: str,
        storage_class: str,
        storage_tier: str,
        retention_policy: str,
        source_file_persisted: bool,
        cleanup_status: str,
        processing_provenance: "UploadProvenance",
        allow_completed_reuse_without_media: bool,
    ) -> tuple[UploadJob, bool]: ...


def _request_user(request: Request) -> UploadCreator:
    return cast(UploadCreator, request.user)


def _serializer_data(serializer: _SerializerDataLike) -> Mapping[str, JsonValue]:
    return serializer.data


def _upload_job_factory_without_center() -> _UploadJobFactoryWithoutCenter:
    ingest_module = import_module("endoreg_db.services.hub.ingest")
    return cast(
        _UploadJobFactoryWithoutCenter,
        getattr(ingest_module, "create_or_reuse_upload_job"),
    )


def _upload_job_factory_with_center() -> _UploadJobFactoryWithCenter:
    ingest_module = import_module("endoreg_db.services.hub.ingest")
    return cast(
        _UploadJobFactoryWithCenter,
        getattr(ingest_module, "create_or_reuse_upload_job"),
    )


def _upload_api_request_mapping(request: Request) -> Mapping[str, str]:
    return cast(Mapping[str, str], request.data)


def _celery_upload_task_available() -> bool:
    try:
        import endoreg_db.tasks as tasks

        return hasattr(tasks, "process_upload_job")
    except ImportError:
        return False


def _celery_upload_task_dispatcher() -> "CeleryTaskDispatcher":
    from endoreg_db.tasks import process_upload_job as process_upload_job_task

    return cast("CeleryTaskDispatcher", process_upload_job_task)


CELERY_AVAILABLE = _celery_upload_task_available()


@dataclass(frozen=True)
class _PreparedApiUpload:
    uploaded_file: UploadedFile
    request_payload: UploadApiRequestPayload
    content_type: str


def _error_response(message: str, *, status_code: int) -> Response:
    return Response({"error": message}, status=status_code)


def _request_uploaded_file(
    request: Request,
) -> tuple[UploadedFile | None, Response | None]:
    if "file" not in request.FILES:
        return None, _error_response(
            'No file provided. Please include a file in the "file" field.',
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    uploaded_file = cast(UploadedFile | object, request.FILES["file"])
    if not isinstance(uploaded_file, UploadedFile):
        return None, _error_response(
            "Uploaded file must be a valid uploaded file.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return uploaded_file, None


def _validate_uploaded_file_metadata(
    uploaded_file: UploadedFile,
    *,
    max_file_size: int,
) -> Response | None:
    uploaded_file_size = uploaded_file.size
    if uploaded_file_size is None:
        return _error_response(
            "Uploaded file size could not be determined.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if uploaded_file_size == 0:
        return _error_response(
            "Uploaded file is empty. Please select a valid file.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if uploaded_file_size > max_file_size:
        return _error_response(
            f"File too large. Maximum size is {max_file_size // (1024**3)} GB.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not _has_valid_upload_filename(uploaded_file):
        return _error_response(
            "Invalid filename. Please ensure the file has a valid name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _has_valid_upload_filename(uploaded_file: UploadedFile) -> bool:
    return bool(uploaded_file.name and uploaded_file.name.strip())


def _validated_upload_payload(
    request: Request,
) -> tuple[UploadApiRequestPayload | None, Response | None]:
    try:
        payload = validate_upload_api_request_payload(
            _upload_api_request_mapping(request)
        )
    except ValueError as exc:
        return None, _error_response(
            str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return payload, None


def _detected_content_type(
    detect_mime_type: Callable[[UploadedFile], str],
    allowed_mime_types: set[str],
    uploaded_file: UploadedFile,
) -> tuple[str | None, Response | None]:
    try:
        content_type = detect_mime_type(uploaded_file)
    except (OSError, ValueError) as exc:
        return None, _error_response(
            f"Could not determine file type: {exc}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if content_type not in allowed_mime_types:
        return None, _error_response(
            f"Unsupported file type: {content_type}. "
            "Allowed types: report, MP4, AVI, MOV, WMV.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return content_type, None


def _prepare_api_upload(
    view: "UploadFileView",
    request: Request,
    *,
    detect_mime_type: Callable[[UploadedFile], str],
) -> tuple[_PreparedApiUpload | None, Response | None]:
    uploaded_file, error = _request_uploaded_file(request)
    if uploaded_file is None:
        return None, error
    metadata_error = _validate_uploaded_file_metadata(
        uploaded_file,
        max_file_size=view.MAX_FILE_SIZE,
    )
    if metadata_error is not None:
        return None, metadata_error
    request_payload, payload_error = _validated_upload_payload(request)
    if request_payload is None:
        return None, payload_error
    content_type, content_type_error = _detected_content_type(
        detect_mime_type,
        view.ALLOWED_MIME_TYPES,
        uploaded_file,
    )
    if content_type is None:
        return None, content_type_error
    return (
        _PreparedApiUpload(
            uploaded_file=uploaded_file,
            request_payload=request_payload,
            content_type=content_type,
        ),
        None,
    )


def _center_resolution_error_response(error: str) -> Response:
    permission_fragments = (
        "Authentication is required",
        "outside the authenticated scope",
        "do not have access",
    )
    status_code = (
        status.HTTP_403_FORBIDDEN
        if any(fragment in error for fragment in permission_fragments)
        else status.HTTP_400_BAD_REQUEST
    )
    return _error_response(error, status_code=status_code)


def _create_or_reuse_api_upload_job(
    request: Request,
    prepared: _PreparedApiUpload,
    *,
    source_center: Center | None,
    processing_provenance: "UploadProvenance",
) -> tuple[UploadJob, bool]:
    payload = prepared.request_payload
    idempotency_key = (
        request.headers.get("Idempotency-Key") or payload.idempotency_key or ""
    )
    if source_center is None:
        return _upload_job_factory_without_center()(
            uploaded_file=prepared.uploaded_file,
            content_type=prepared.content_type,
            created_by=_request_user(request),
            source_system=payload.source_system,
            content_hash="",
            idempotency_key=idempotency_key,
            ingest_mode=UploadJob.IngestMode.API,
            storage_class=UploadJob.StorageClass.INGEST,
            storage_tier=UploadJob.StorageTier.UPLOAD_API,
            retention_policy=UploadJob.RetentionPolicy.PRESERVE_SOURCE,
            source_file_persisted=True,
            cleanup_status=UploadJob.CleanupStatus.PENDING,
            processing_provenance=processing_provenance,
            allow_completed_reuse_without_media=False,
        )
    return _upload_job_factory_with_center()(
        uploaded_file=prepared.uploaded_file,
        content_type=prepared.content_type,
        created_by=_request_user(request),
        source_center=source_center,
        source_system=payload.source_system,
        content_hash="",
        idempotency_key=idempotency_key,
        ingest_mode=UploadJob.IngestMode.API,
        storage_class=UploadJob.StorageClass.INGEST,
        storage_tier=UploadJob.StorageTier.UPLOAD_API,
        retention_policy=UploadJob.RetentionPolicy.PRESERVE_SOURCE,
        source_file_persisted=True,
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        processing_provenance=processing_provenance,
        allow_completed_reuse_without_media=False,
    )


def _start_created_upload_job(
    upload_job: UploadJob, *, created: bool
) -> Response | None:
    if not created:
        return None
    try:
        if CELERY_AVAILABLE:
            ingest.start_upload_job_processing(
                upload_job=upload_job,
                task_dispatcher=_celery_upload_task_dispatcher(),
            )
        else:
            ingest.start_upload_job_processing(upload_job=upload_job)
    except (
        DatabaseError,
        KombuOperationalError,
        OSError,
        RuntimeError,
        TypeError,
    ) as exc:
        return _error_response(
            f"Failed to start processing: {exc}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return None


def _upload_success_response(upload_job: UploadJob, *, created: bool) -> Response:
    upload_job_id = getattr(upload_job, "id", None)
    upload_id = str(upload_job_id) if upload_job_id is not None else ""
    return Response(
        {
            "upload_id": upload_id,
            "status_url": reverse("api:upload_status", kwargs={"id": upload_id}),
            "message": "Upload job created successfully",
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


def _perform_api_upload(request: Request, prepared: _PreparedApiUpload) -> Response:
    source_center, _allowed_center_id, center_error, raw_context = (
        ingest.resolve_api_upload_context(
            user=_request_user(request),
            center_key=prepared.request_payload.center_key,
            center_name=prepared.request_payload.center_name,
        )
    )
    if center_error:
        return _center_resolution_error_response(center_error)
    processing_provenance = cast(
        "UploadProvenance",
        {"entrypoint": "api", **cast(UploadContext, raw_context)},
    )
    upload_job, created = _create_or_reuse_api_upload_job(
        request,
        prepared,
        source_center=source_center,
        processing_provenance=processing_provenance,
    )
    processing_error = _start_created_upload_job(upload_job, created=created)
    if processing_error is not None:
        return processing_error
    return _upload_success_response(upload_job, created=created)


@method_decorator(csrf_exempt, name="dispatch")
class UploadFileView(APIView):
    """
    Handle file uploads (POST /api/upload/).

    Accepts multipart/form-data with a 'file' field containing report or video files.
    Creates an UploadJob and starts processing.

    Returns:
        201 Created: {"upload_id": "<uuid>", "status_url": "/api/upload/<uuid>/status/"}
        400 Bad Request: File validation errors
    """

    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    # Maximum file size (1 GiB)
    MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB in bytes

    # Allowed MIME types
    ALLOWED_MIME_TYPES = {
        "application/pdf",
        "video/mp4",
        "video/avi",
        "video/quicktime",
        "video/x-msvideo",
        "video/x-ms-wmv",
    }

    def post(
        self,
        request: Request,
        *args: str,
        **kwargs: str,
    ) -> Response:
        """
        Handle file upload and create processing job.
        """
        prepared, preparation_error = _prepare_api_upload(
            self,
            request,
            detect_mime_type=self._detect_mime_type,
        )
        if prepared is None:
            assert preparation_error is not None
            return preparation_error
        try:
            return _perform_api_upload(request, prepared)
        except PermissionDenied as exc:
            return _error_response(
                str(exc),
                status_code=status.HTTP_403_FORBIDDEN,
            )
        except (
            AttributeError,
            DatabaseError,
            ImportError,
            KombuOperationalError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            return _error_response(
                f"Failed to create upload job: {exc}",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _detect_mime_type(self, uploaded_file: UploadedFile) -> str:
        """
        Detect MIME type using python-magic as primary method,
        fallback to mimetypes module.
        """
        uploaded_file_name = uploaded_file.name

        try:
            # Reset file pointer
            uploaded_file.seek(0)

            # Try python-magic first (more reliable) if available
            if MAGIC_AVAILABLE and _magic is not None:
                try:
                    # Read first chunk for magic detection
                    chunk = uploaded_file.read(2048)
                    uploaded_file.seek(0)  # Reset again

                    mime_type = _magic.from_buffer(chunk, mime=True)
                    if mime_type and mime_type != "application/octet-stream":
                        return mime_type
                except Exception:
                    pass  # Fall back to mimetypes

            # Fallback to mimetypes module
            uploaded_file_name = uploaded_file_name or ""
            mime_guess, _ = mimetypes.guess_type(uploaded_file_name)
            if isinstance(mime_guess, str):
                return mime_guess

            # Last resort - check file extension
            normalized_name = uploaded_file_name.lower()
            if normalized_name.endswith(".pdf"):
                return "application/pdf"
            elif normalized_name.endswith((".mp4", ".m4v")):
                return "video/mp4"
            elif normalized_name.endswith(".avi"):
                return "video/avi"
            elif normalized_name.endswith((".mov", ".qt")):
                return "video/quicktime"
            elif normalized_name.endswith(".wmv"):
                return "video/x-ms-wmv"

            raise ValueError("Could not determine file type")

        finally:
            # Ensure file pointer is reset
            uploaded_file.seek(0)


class UploadStatusView(APIView):
    """
    Get upload job status (GET /api/upload/<uuid>/status/).

    Returns current processing status and relevant metadata.
    Should be polled every 2 seconds by the frontend.

    Returns:
        200 OK: Status information
        404 Not Found: Upload job not found
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(
        self,
        request: Request,
        id: str,
        *args: str,
        **kwargs: str,
    ) -> Response:
        """
        Return the current status of an upload job.
        """
        try:
            # Look up upload job by UUID
            upload_job = UploadJob.objects.select_related(
                "sensitive_meta",
                "source_center",
            ).get(id=id)

            from endoreg_db.services.center_access import resolve_allowed_center_ids

            allowed_center_ids = resolve_allowed_center_ids(
                getattr(request, "user", None)
            )
            source_center_id = getattr(upload_job, "source_center_id", None)
            if (
                allowed_center_ids is not None
                and source_center_id is not None
                and source_center_id not in allowed_center_ids
            ):
                raise Http404("Upload job not found")
            if allowed_center_ids == frozenset():
                raise PermissionDenied("You do not have access to upload jobs.")

            # Serialize the response
            serializer = UploadJobStatusSerializer(upload_job)
            serializer_payload = _serializer_data(cast(_SerializerDataLike, serializer))
            return Response(dict(serializer_payload), status=status.HTTP_200_OK)

        except UploadJob.DoesNotExist:
            raise Http404("Upload job not found")
        except (Http404, PermissionDenied):
            raise
        except (DatabaseError, RuntimeError, TypeError, ValueError) as exc:
            return Response(
                {"error": f"Failed to get upload status: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
