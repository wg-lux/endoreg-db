# endoreg_db/api/views/anonymization_overview.py

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from django.db.models import Q
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
from endoreg_db.services.anonymization import AnonymizationService
from endoreg_db.services.polling_coordinator import (
    PollingCoordinator,
    ProcessingLockContext,
)
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from endoreg_db.models import VideoFile, RawPdfFile, UploadJob
from ...serializers import FileOverviewSerializer, VoPPatientDataSerializer
from django.http import JsonResponse
from endoreg_db.utils.operation_log import (
    record_operation,
    ACTION_ANONYMIZATION_START,
    STATUS_NOT_STARTED,
    STATUS_PROCESSING,
)


from endoreg_db.authz.permissions import PolicyPermission  #  import RBAC
import logging

logger = logging.getLogger(__name__)
PERMS = DEBUG_PERMISSIONS  # shorten


# ---------- overview ----------------------------------------------------
class NoPagination(PageNumberPagination):
    page_size = None


def _overview_content_hash(item) -> str:
    if isinstance(item, VideoFile):
        return getattr(item, "video_hash", "") or ""
    if isinstance(item, RawPdfFile):
        return getattr(item, "pdf_hash", "") or ""
    return ""


def _attach_overview_upload_jobs(items):
    sensitive_meta_ids = {
        sensitive_meta_id
        for item in items
        if (sensitive_meta_id := getattr(item, "sensitive_meta_id", None)) is not None
    }
    content_hashes = {
        content_hash for item in items if (content_hash := _overview_content_hash(item))
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

    by_sensitive_meta_id = {}
    by_content_hash = {}
    for upload_job in upload_jobs:
        if (
            upload_job.sensitive_meta_id
            and upload_job.sensitive_meta_id not in by_sensitive_meta_id
        ):
            by_sensitive_meta_id[upload_job.sensitive_meta_id] = upload_job
        if upload_job.content_hash and upload_job.content_hash not in by_content_hash:
            by_content_hash[upload_job.content_hash] = upload_job

    for item in items:
        sensitive_meta_id = getattr(item, "sensitive_meta_id", None)
        content_hash = _overview_content_hash(item)
        upload_job = (
            by_sensitive_meta_id.get(sensitive_meta_id)
            if sensitive_meta_id is not None
            else None
        ) or by_content_hash.get(content_hash)
        setattr(item, "_overview_upload_job", upload_job)


class AnonymizationOverviewView(ListAPIView):
    """
    GET /api/anonymization/items/overview/
    --------------------------------------
    Returns a flat list (Video + PDF) ordered by newest upload first.
    """

    serializer_class = FileOverviewSerializer
    # permission_classes = DEBUG_PERMISSIONS
    permission_classes = [PolicyPermission]
    pagination_class = NoPagination

    def get_queryset(self):
        """
        Returns a combined queryset of VideoFile and RawPdfFile instances.
        """
        # 1) VideoFile queryset - only fields that exist on VideoFile
        qs_video = (
            VideoFile.objects.select_related("state", "sensitive_meta")
            .prefetch_related("label_video_segments__state")
            .only(
                "id",
                "original_file_name",
                "raw_file",
                "uploaded_at",
                "video_hash",
                "state",
                "sensitive_meta",
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

        combined = list(qs_video) + list(qs_pdf)
        _attach_overview_upload_jobs(combined)

        def _created_at(item):
            if isinstance(item, VideoFile):
                return getattr(item, "uploaded_at", None)
            if isinstance(item, RawPdfFile):
                return getattr(item, "date_created", None)
            return None

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
    def post(self, request, item_id: int):
        payload = request.data or {}
        payload.setdefault("is_verified", True)

        # Try Video first
        video = VideoFile.objects.filter(pk=item_id).first()
        if video:
            video_state = video.get_or_create_state()
            video_meta = video.meta if isinstance(video.meta, dict) else {}
            if getattr(video_state, "processing_error", False) or (
                video_meta.get("integrity_status") == "lost"
            ):
                return Response(
                    {"error": "Video is marked failed/lost by media integrity."},
                    status=status.HTTP_409_CONFLICT,
                )
            ok = video.validate_metadata_annotation(payload)
            if not ok:
                return Response(
                    {"error": "Video validation failed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"message": "Video validated."}, status=status.HTTP_200_OK)

        # Then PDF
        pdf = RawPdfFile.objects.filter(pk=item_id).first()
        if pdf:
            ok = pdf.validate_metadata_annotation(payload)
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
def anonymization_status(request, file_id: int):
    """
    Get anonymization status with polling rate limiting.
    """
    # Ermittele erst den echten Typ und Status
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    file_type = (
        info.get("media_type") or info.get("mediaType") or info.get("type") or "video"
    )

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
        info.get("anonymization_status")
        or info.get("anonymizationStatus")
        or info.get("status")
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
            "anonymizationStatus": status_val,
            "anonymization_status": status_val,
            "integrity_status": info.get("integrity_status", ""),
            "integrity_error": info.get("integrity_error", ""),
            "processing_locked": processing_locked_derived,
        }
    )


# ---------- start with processing lock ----------------------------------
@api_view(["POST"])
@permission_classes(PERMS)
def start_anonymization(request, file_id: int):
    """
    Start anonymization with processing lock to prevent duplicates.
    """
    # First check what type of file this is
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    file_type = info.get("media_type") or info.get("mediaType") or "unknown"
    status_val = (
        info.get("anonymization_status")
        or info.get("anonymizationStatus")
        or info.get("status")
        or "not_started"
    )
    if info.get("integrity_status") == "lost" or status_val == "failed":
        return Response(
            {
                "detail": "File is marked failed/lost and cannot be anonymized",
                "file_id": file_id,
                "file_type": file_type,
                "integrity_status": info.get("integrity_status", ""),
                "integrity_error": info.get("integrity_error", ""),
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


# ---------- current with coordination ------------------------------------
@api_view(["GET", "POST", "PUT"])
@permission_classes(DEBUG_PERMISSIONS)
def anonymization_current(request, file_id):
    """
    Set current file for validation and return patient data
    """
    # Try to find the file in VideoFile first
    try:
        video_file = VideoFile.objects.select_related("sensitive_meta").get(id=file_id)
        serializer = VoPPatientDataSerializer(video_file, context={"request": request})
        return Response(serializer.data)
    except VideoFile.DoesNotExist:
        pass
    # Try to find the file in RawPdfFile
    try:
        pdf_file = RawPdfFile.objects.select_related("sensitive_meta").get(id=file_id)
        serializer = VoPPatientDataSerializer(pdf_file, context={"request": request})
        return Response(serializer.data)

    except RawPdfFile.DoesNotExist:
        pass

    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error in set_current_for_validation: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

    return JsonResponse({"status": "error", "message": "File not found"}, status=404)


# ---------- polling coordinator info ------------------------------------
@api_view(["GET"])
@permission_classes(DEBUG_PERMISSIONS)
def polling_coordinator_info(request):
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
def clear_processing_locks(request):
    """
    DELETE /api/anonymization/clear-locks/
    Emergency endpoint to clear all processing locks
    """
    try:
        file_type = request.query_params.get("type", None)
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
def has_raw_video_file(request, file_id: int):
    """
    Return whether the video still has a raw video file.
    """
    try:
        video = VideoFile.get_video_by_pk(pk=file_id)
    except VideoFile.DoesNotExist:
        return Response(
            {"detail": "Video not found", "file_id": file_id},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response({"file_id": file_id, "has_raw": video.has_raw})
