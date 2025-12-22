# endoreg_db/api/views/anonymization_overview.py

import logging

from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission  # import RBAC
from endoreg_db.models import RawPdfFile, VideoFile
from endoreg_db.services.anonymization import AnonymizationService
from endoreg_db.services.polling_coordinator import (
    PollingCoordinator,
    ProcessingLockContext,
)
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

from ...serializers import FileOverviewSerializer, VoPPatientDataSerializer
from endoreg_db.utils.operation_log import record_operation


logger = logging.getLogger(__name__)
PERMS = DEBUG_PERMISSIONS  # shorten


# ---------- overview ----------------------------------------------------
class NoPagination(PageNumberPagination):
    page_size = None


class AnonymizationOverviewView(APIView):
    """
    GET /api/anonymization/items/overview/
    
    Fetches both VideoFiles and RawPdfFiles, combines them into a single 
    chronological list, and serializes them for the Anonymization Dashboard.
    """
    permission_classes = [PolicyPermission]

    def get(self, request) -> Response:
        # 1. Fetch Videos
        # select_related 'state' and 'sensitive_meta' to allow the serializer 
        # to access them without triggering extra DB queries.
        videos = VideoFile.objects.select_related(
            "state", 
            "sensitive_meta"
        ).only(
            "id", 
            "original_file_name", 
            "raw_file",       # Needed for filename and size
            "uploaded_at",    # Needed for createdAt
            "state",          # Needed for anonymizationStatus
            "sensitive_meta"  # Needed for sensitiveMetaId
        )

        # 2. PDFs: REMOVED 'text', 'anonymized_text'. ADDED 'state'.
        pdfs = RawPdfFile.objects.select_related(
            "state",          # <--- CRITICAL: Prevents N+1 queries
            "sensitive_meta"
        ).only(
            "id", 
            "file",           # Needed for filename and size
            "date_created",   # Needed for createdAt
            "state",          # Needed for anonymizationStatus
            "sensitive_meta"  # Needed for sensitiveMetaId
        )

        # 3. Combine in Python
        combined_list = list(videos) + list(pdfs)

        # 4. Sort by Date (Newest first)
        # We use a lambda to handle the different date field names
        combined_list.sort(
            key=lambda obj: obj.uploaded_at if isinstance(obj, VideoFile) else obj.date_created,
            reverse=True
        )

        # 5. Serialize and Return
        # 'many=True' tells DRF to iterate over the list and call to_representation for each item
        serializer = FileOverviewSerializer(combined_list, many=True)
        
        return Response(serializer.data)

# ---------- status with polling protection ------------------------------
@api_view(["GET"])
@permission_classes(PERMS)
def anonymization_status(request, file_id: int, kind: str) -> Response:
    """
    Get anonymization status with polling rate limiting.
    """
    # Ermittele erst den echten Typ und Status
    info = AnonymizationService.get_status(file_id, kind=kind)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    file_type = info.get("mediaType") or info.get("type") or "video"

    # Wende Rate-Limiting auf den echten Typ an (nicht auf einen evtl. falschen request-Parameter)
    if not PollingCoordinator.can_check_status(file_id, file_type):
        remaining_seconds = PollingCoordinator.get_remaining_cooldown_seconds(
            file_id, file_type
        )
        response_data = {
            "detail": "Status check rate limited. Please wait before checking again.",
            "file_id": file_id,
            "cooldown_active": True,
            "retry_after": remaining_seconds,
        }
        response = Response(response_data, status=status.HTTP_429_TOO_MANY_REQUESTS)
        response["Retry-After"] = str(remaining_seconds)
        return response

    status_val = info.get("anonymizationStatus") or info.get("status") or "not_started"

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

    file_type = info.get("mediaType") or "unknown"
    status_before = (
        info.get("anonymizationStatus") or info.get("status") or "not_started"
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
            info_after = AnonymizationService.get_status(file_id) or {}
        except Exception:
            logger.exception(
                "Failed to refresh anonymization status for file %s", file_id
            )
            info_after = {}

        status_after = (
            info_after.get("anonymizationStatus")
            or info_after.get("status")
            or status_before
        )

        # 🔐 Write operation log
        record_operation(
            request,
            action="anonymization.start",
            resource_type=kind,  # 'video' or 'pdf' as returned by service.start
            resource_id=file_id,
            status_before=str(status_before),
            status_after=str(status_after),
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
@permission_classes(DEBUG_PERMISSIONS)
def has_raw_video_file(request, file_id):
    """
    GET /api/anonymization/{file_id}/has-raw/
    Check if a raw video file exists for the given file ID
    """
    exists = VideoFile.objects.filter(id=file_id, raw_file__isnull=False).exists()
    return Response({"file_id": file_id, "has_raw_file": exists})
