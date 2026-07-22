# endoreg_db/api/views/anonymization_overview.py

from typing import Any, Protocol, cast

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
from endoreg_db.services.anonymization import AnonymizationService
from endoreg_db.services.polling_coordinator import (
    PollingCoordinator,
    ProcessingLockContext,
)
from endoreg_db.services.raw_pdf_files import validate_report_metadata_annotation
from endoreg_db.services.center_access import resolve_allowed_center_ids
from endoreg_db.services.hub import hub_mode_enabled
from endoreg_db.services.video_files import (
    get_or_create_video_state,
    get_video_by_pk,
    validate_video_metadata_annotation,
)
from endoreg_db.views.access_control import (
    filter_video_read_queryset,
    has_cross_center_hub_processed_access,
)
from endoreg_db.serializers.misc.file_overview import (
    CrossCenterProcessedOverviewSerializer,
    FileOverviewSerializer,
)
from ...serializers import VoPPatientDataSerializer
from endoreg_db.utils.operation_log import (
    record_operation,
    ACTION_ANONYMIZATION_START,
    STATUS_NOT_STARTED,
    STATUS_PROCESSING,
)
import logging

from lx_dtypes.models.contracts.anonymization_overview import (
    AnonymizationStatusInfoData,
)
from endoreg_db.services.raw_pdf_files.metadata import ReportMetaJsonObject
from endoreg_db.services.video_files.metadata import VideoTextMetaPayload

logger = logging.getLogger(__name__)
PERMS = DEBUG_PERMISSIONS  # shorten


# ---------- overview ----------------------------------------------------
class NoPagination(PageNumberPagination):
    page_size = None


class _OverviewItem(Protocol):
    sensitive_meta_id: int | None


class _OverviewUploadJobLike(Protocol):
    sensitive_meta_id: int | None
    content_hash: str


class _OverviewUploadJobCarrier(Protocol):
    overview_upload_job: UploadJob | None


class _SerializerDataCarrier(Protocol):
    data: dict[str, object]


def _overview_content_hash(item: VideoFile | RawPdfFile) -> str:
    if isinstance(item, VideoFile):
        return getattr(item, "video_hash", "") or ""
    return getattr(item, "pdf_hash", "") or ""


def _attach_overview_upload_jobs(
    items: list[VideoFile | RawPdfFile],
) -> None:
    sensitive_meta_ids = {
        cast(_OverviewItem, item).sensitive_meta_id
        for item in items
        if cast(_OverviewItem, item).sensitive_meta_id is not None
    }
    content_hashes = {
        _overview_content_hash(item) for item in items if _overview_content_hash(item)
    }

    if not sensitive_meta_ids and not content_hashes:
        return

    filters = Q()
    if sensitive_meta_ids:
        filters |= Q(sensitive_meta_id__in=sensitive_meta_ids)
    if content_hashes:
        filters |= Q(content_hash__in=content_hashes)

    upload_jobs = (
        UploadJob.objects.select_related("source_center")
        .filter(filters)
        .order_by("-updated_at", "-created_at")
    )

    by_sensitive_meta_id: dict[int, UploadJob] = {}
    by_content_hash: dict[str, UploadJob] = {}
    for upload_job in upload_jobs:
        upload_job_like = cast(_OverviewUploadJobLike, upload_job)
        if (
            upload_job_like.sensitive_meta_id
            and upload_job_like.sensitive_meta_id not in by_sensitive_meta_id
        ):
            by_sensitive_meta_id[upload_job_like.sensitive_meta_id] = upload_job
        if (
            upload_job_like.content_hash
            and upload_job_like.content_hash not in by_content_hash
        ):
            by_content_hash[upload_job_like.content_hash] = upload_job

    for item in items:
        overview_item = cast(_OverviewItem, item)
        sensitive_meta_id = overview_item.sensitive_meta_id
        content_hash = _overview_content_hash(item)
        upload_job = (
            by_sensitive_meta_id.get(sensitive_meta_id)
            if sensitive_meta_id is not None
            else None
        ) or by_content_hash.get(content_hash)
        carrier = cast(_OverviewUploadJobCarrier, item)
        setattr(carrier, "_overview_upload_job", upload_job)


class AnonymizationOverviewView(APIView):
    """
    GET /api/anonymization/items/overview/
    --------------------------------------
    Returns a flat list (Video + PDF) ordered by newest upload first.
    """

    permission_classes = [PolicyPermission]

    def get(self, request: Request) -> Response:
        allowed_center_ids = resolve_allowed_center_ids(request.user)
        if allowed_center_ids == frozenset() and not hub_mode_enabled():
            raise PermissionDenied(
                "No center membership is assigned. Contact an administrator."
            )
        serializer_data = [
            cast(
                dict[str, object],
                cast(
                    Any,
                    CrossCenterProcessedOverviewSerializer(item)
                    if isinstance(item, VideoFile)
                    and has_cross_center_hub_processed_access(
                        user=request.user,
                        obj=item,
                    )
                    else FileOverviewSerializer(
                        cast(Any, item),
                        context={"request": request},
                    ),
                ).data,
            )
            for item in self.get_queryset(request_user=request.user)
        ]
        return Response(
            serializer_data,
            status=status.HTTP_200_OK,
        )

    def get_queryset(
        self,
        *,
        request_user: object | None = None,
    ) -> list[VideoFile | RawPdfFile]:
        """
        Returns a combined queryset of VideoFile and RawPdfFile instances.
        """
        # 1) VideoFile queryset - only fields that exist on VideoFile
        qs_video = (
            VideoFile.objects.select_related("state", "sensitive_meta", "center")
            .prefetch_related("label_video_segments__state", "hls_artifacts")
            .only(
                "id",
                "original_file_name",
                "processed_file",
                "raw_file",
                "uploaded_at",
                "video_hash",
                "state",
                "sensitive_meta",
                "center",
                "center__center_key",
                "center__display_name",
            )
        )
        # 2) RawPdfFile queryset - only fields that exist on RawPdfFile
        qs_pdf = RawPdfFile.objects.select_related(
            "sensitive_meta", "anonym_examination_report__type"
        ).only(
            "id",
            "file",
            "date_created",
            "text",
            "anonymized_text",
            "pdf_hash",
            "sensitive_meta",
            "raw_meta",
            "anonym_examination_report",
            "anonym_examination_report__type",
            "anonym_examination_report__type__name",
            "sensitive_meta__patient_hash",
            "sensitive_meta__examination_hash",
            "sensitive_meta__pseudo_patient",
            "sensitive_meta__pseudo_examination",
        )

        allowed_center_ids = resolve_allowed_center_ids(request_user)
        qs_video = filter_video_read_queryset(
            queryset=qs_video,
            user=request_user,
        )
        if allowed_center_ids == frozenset():
            qs_pdf = qs_pdf.none()
        elif allowed_center_ids is not None:
            qs_pdf = qs_pdf.filter(center_id__in=allowed_center_ids)

        combined = list(qs_video) + list(qs_pdf)
        _attach_overview_upload_jobs(combined)

        def _created_at(item: VideoFile | RawPdfFile):
            if isinstance(item, VideoFile):
                return getattr(item, "uploaded_at", None)
            return getattr(item, "date_created", None)

        combined.sort(
            key=lambda item: (_created_at(item) is not None, _created_at(item)),
            reverse=True,
        )
        return combined


class AnonymizationValidateView(APIView):
    """
    POST /api/anonymization/<int:item_id>/validate/
    Body: {
      // common SensitiveMeta fields (snake_case):
      "patient_first_name": "...",
      "patient_last_name":  "...",
      "patient_dob":        "YYYY-MM-DD",
      "examination_date":   "YYYY-MM-DD",
      "casenumber":         "...",
      "anonymized_text":    "...",   # only for PDFs; ignored by videos
      "is_verified": true            # optional; defaults to true here
    }
    """

    @transaction.atomic
    def post(self, request: Request, item_id: int) -> Response:
        payload = request.data
        payload.setdefault("is_verified", True)

        # Try Video first
        video = VideoFile.objects.filter(pk=item_id).first()
        if video:
            video_state = get_or_create_video_state(video)
            video_meta = cast(dict[str, object], getattr(video, "meta", {}))
            if getattr(video_state, "processing_error", False) or (
                video_meta.get("integrity_status") == "lost"
            ):
                return Response(
                    {"error": "Video is marked failed/lost by media integrity."},
                    status=status.HTTP_409_CONFLICT,
                )
            ok = validate_video_metadata_annotation(
                video, cast(VideoTextMetaPayload, payload)
            )
            if not ok:
                return Response(
                    {"error": "Video validation failed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"message": "Video validated."}, status=status.HTTP_200_OK)

        # Then PDF
        pdf = RawPdfFile.objects.filter(pk=item_id).first()
        if pdf:
            ok = validate_report_metadata_annotation(
                pdf, cast(ReportMetaJsonObject, payload)
            )
            if not ok:
                return Response(
                    {"error": "PDF validation failed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"message": "PDF validated."}, status=status.HTTP_200_OK)

        return Response(
            {"error": f"Item {item_id} not found as video or pdf."},
            status=status.HTTP_404_NOT_FOUND,
        )


# ---------- status with polling protection ------------------------------
@api_view(["GET"])
@permission_classes(PERMS)
def anonymization_status(request: Request, file_id: int) -> Response:
    """
    Get anonymization status with polling rate limiting.
    """
    # Ermittele erst den echten Typ und Status
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    status_info = cast(AnonymizationStatusInfoData, info)
    file_type = status_info.get("media_type") or status_info.get("type") or "video"

    # Wende Rate-Limiting auf den echten Typ an (nicht auf einen evtl. falschen request-Parameter)
    if not PollingCoordinator.can_check_status(file_id, file_type):
        return Response(
            {
                "detail": "Status check rate limited. Please wait before checking again.",
                "file_id": file_id,
                "cooldown_active": True,
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    status_val = (
        status_info.get("anonymization_status")
        or status_info.get("status")
        or "not_started"
    )

    # processing_locked als Ableitung des Status interpretieren
    processing_statuses = {
        "processing_anonymization",
        "extracting_frames",
        "predicting_segments",
    }
    processing_locked_derived = status_val in processing_statuses

    return Response(
        {
            "file_id": file_id,
            "file_type": file_type,
            "anonymization_status": status_val,
            "integrity_status": status_info.get("integrity_status", ""),
            "integrity_error": status_info.get("integrity_error", ""),
            "processing_locked": processing_locked_derived,
        }
    )


# ---------- start with processing lock ----------------------------------
@api_view(["POST"])
@permission_classes(PERMS)
def start_anonymization(request: Request, file_id: int) -> Response:
    """
    Start anonymization with processing lock to prevent duplicates.
    """
    # First check what type of file this is
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    status_info = cast(AnonymizationStatusInfoData, info)
    file_type = status_info.get("media_type") or "unknown"
    status_val = (
        status_info.get("anonymization_status")
        or status_info.get("status")
        or "not_started"
    )
    if status_info.get("integrity_status") == "lost" or status_val == "failed":
        return Response(
            {
                "detail": "File is marked failed/lost and cannot be anonymized",
                "file_id": file_id,
                "file_type": file_type,
                "integrity_status": status_info.get("integrity_status", ""),
                "integrity_error": status_info.get("integrity_error", ""),
            },
            status=status.HTTP_409_CONFLICT,
        )
    # Use processing lock context to prevent duplicate processing
    with ProcessingLockContext(file_id, file_type) as lock:
        if not lock.acquired:
            return Response(
                {
                    "detail": "File is already being processed by another request",
                    "file_id": file_id,
                    "file_type": file_type,
                    "processing_locked": True,
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Proceed with starting anonymization
        service = AnonymizationService()
        kind = service.start(file_id)
        if not kind:
            return Response(
                {"detail": "Failed to start anonymization"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Re-read status AFTER starting
        try:
            AnonymizationService.get_status(file_id)
        except Exception:
            logger.exception(
                "Failed to refresh anonymization status for file %s", file_id
            )

        # 🔐 Write operation log
        record_operation(
            request,
            action=ACTION_ANONYMIZATION_START,
            resource_type=kind,  # 'video' or 'pdf' as returned by service.start
            resource_id=file_id,
            status_before=STATUS_NOT_STARTED,
            status_after=STATUS_PROCESSING,
            meta={
                "file_type_from_status": file_type,
            },
        )

        return Response(
            {
                "detail": f"Anonymization started for {kind} file",
                "file_id": file_id,
                "file_type": kind,
                "processing_locked": True,
            }
        )
    return Response(
        {
            "detail": "Unable to start anonymization",
            "file_id": file_id,
            "file_type": status_info.get("media_type") or "unknown",
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# ---------- current with coordination ------------------------------------
@api_view(["GET", "POST", "PUT"])
@permission_classes(DEBUG_PERMISSIONS)
def anonymization_current(request: Request, file_id: int) -> Response:
    """
    Set current file for validation and return patient data
    """
    # Try to find the file in VideoFile first
    try:
        video_file = VideoFile.objects.select_related("sensitive_meta").get(id=file_id)
        serializer = cast(
            _SerializerDataCarrier,
            VoPPatientDataSerializer(video_file, context={"request": request}),
        )
        return Response(serializer.data)
    except VideoFile.DoesNotExist:
        pass
    # Try to find the file in RawPdfFile
    try:
        pdf_file = RawPdfFile.objects.select_related("sensitive_meta").get(id=file_id)
        serializer = cast(
            _SerializerDataCarrier,
            VoPPatientDataSerializer(pdf_file, context={"request": request}),
        )
        return Response(serializer.data)

    except RawPdfFile.DoesNotExist:
        pass

    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error in set_current_for_validation: {e}")
        return Response({"status": "error", "message": str(e)}, status=500)

    return Response({"status": "error", "message": "File not found"}, status=404)


# ---------- polling coordinator info ------------------------------------
@api_view(["GET"])
@permission_classes(DEBUG_PERMISSIONS)
def polling_coordinator_info(request: Request) -> Response:
    """
    GET /api/anonymization/polling-info/
    Get information about polling coordinator status
    """
    try:
        info = PollingCoordinator.get_processing_locks_info()
        return Response(info)
    except Exception as e:
        logger.error(f"Error getting polling coordinator info: {e}")
        return Response(
            {"error": "Failed to get coordinator info"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ---------- emergency lock management -----------------------------------
@api_view(["DELETE"])
@permission_classes(DEBUG_PERMISSIONS)
def clear_processing_locks(request: Request) -> Response:
    """
    DELETE /api/anonymization/clear-locks/
    Emergency endpoint to clear all processing locks
    """
    try:
        file_type = request.query_params.get("type")
        cleared_count = PollingCoordinator.clear_all_locks(file_type)
        return Response(
            {
                "detail": "Processing locks cleared",
                "cleared_count": cleared_count,
                "file_type_filter": file_type,
            }
        )
    except Exception as e:
        logger.error(f"Error clearing processing locks: {e}")
        return Response(
            {"error": "Failed to clear locks"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes(PERMS)
def has_raw_video_file(request: Request, file_id: int) -> Response:
    """
    Return whether the video still has a raw video file.
    """
    try:
        video = get_video_by_pk(pk=file_id)
    except VideoFile.DoesNotExist:
        return Response(
            {"detail": "Video not found", "file_id": file_id},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response({"file_id": file_id, "has_raw": video.has_raw})
