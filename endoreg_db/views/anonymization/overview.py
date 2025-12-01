# endoreg_db/api/views/anonymization_overview.py

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS
from endoreg_db.services.anonymization import AnonymizationService
from endoreg_db.services.polling_coordinator import PollingCoordinator, ProcessingLockContext
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from endoreg_db.models import VideoFile, RawPdfFile
from ...serializers import FileOverviewSerializer, VoPPatientDataSerializer
from django.http import JsonResponse

from endoreg_db.authz.permissions import PolicyPermission  #  import RBAC
import logging
logger = logging.getLogger(__name__)
PERMS = DEBUG_PERMISSIONS   # shorten

# ---------- overview ----------------------------------------------------
class NoPagination(PageNumberPagination):
    page_size = None


class AnonymizationOverviewView(ListAPIView):
    """
    GET /api/anonymization/items/overview/
    --------------------------------------
    Returns a flat list (Video + PDF) ordered by newest upload first.
    """
    serializer_class = FileOverviewSerializer
    #permission_classes = DEBUG_PERMISSIONS   
    permission_classes = [PolicyPermission]
    pagination_class = NoPagination

    def get_queryset(self):
        """
        Provide a flat list combining video and PDF file records for the anonymization overview.
        
        This method retrieves VideoFile and RawPdfFile querysets with a restricted set of selected fields and returns them concatenated into a single list. VideoFile instances appear first in the list followed by RawPdfFile instances.
        
        Returns:
            list: A list of model instances — first `VideoFile` objects, then `RawPdfFile` objects — with only the selected fields fetched from the database.
        """
        # 1) VideoFile queryset - only fields that exist on VideoFile
        qs_video = (
            VideoFile.objects
            .select_related("state", "sensitive_meta")
            .prefetch_related("label_video_segments__state")
            .only("id", "original_file_name", "raw_file", "uploaded_at", "state", "sensitive_meta")
        )
        # 2) RawPdfFile queryset - only fields that exist on RawPdfFile
        qs_pdf = (
            RawPdfFile.objects
            .select_related("sensitive_meta")
            .only("id", "file", "date_created", 
                "text", "anonymized_text",     
                "sensitive_meta")

        )

        return list(qs_video) + list(qs_pdf)
    
# ---------- status with polling protection ------------------------------
@api_view(["GET"])
@permission_classes(PERMS)
def anonymization_status(request, file_id: int):
    """
    Retrieve anonymization status for a file while enforcing polling rate limits.
    
    Parameters:
        file_id (int): Identifier of the file whose anonymization status is requested.
    
    Returns:
        dict: One of the following JSON payloads wrapped in a Response:
            - 404: {"detail": "File not found"} when the file does not exist.
            - 429: {
                "detail": "Status check rate limited. Please wait before checking again.",
                "file_id": <int>,
                "cooldown_active": True,
                "retry_after": <seconds>
              } and a "Retry-After" header when polling is rate-limited.
            - 200: {
                "file_id": <int>,
                "file_type": <str>,
                "anonymizationStatus": <str>,
                "processing_locked": <bool>
              } with the current anonymization status and a boolean indicating whether processing is considered locked.
    """
    # Ermittele erst den echten Typ und Status
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    file_type = info.get("mediaType") or info.get("type") or "video"

    # Wende Rate-Limiting auf den echten Typ an (nicht auf einen evtl. falschen request-Parameter)
    if not PollingCoordinator.can_check_status(file_id, file_type):
        remaining_seconds = PollingCoordinator.get_remaining_cooldown_seconds(file_id, file_type)
        response_data = {
            "detail": "Status check rate limited. Please wait before checking again.",
            "file_id": file_id,
            "cooldown_active": True,
            "retry_after": remaining_seconds
        }
        response = Response(response_data, status=status.HTTP_429_TOO_MANY_REQUESTS)
        response["Retry-After"] = str(remaining_seconds)
        return response

    status_val = info.get("anonymizationStatus") or info.get("status") or "not_started"

    # processing_locked als Ableitung des Status interpretieren
    processing_statuses = {"processing_anonymization", "extracting_frames", "predicting_segments"}
    processing_locked_derived = status_val in processing_statuses

    return Response({
        "file_id": file_id,
        "file_type": file_type,
        "anonymizationStatus": status_val,
        "processing_locked": processing_locked_derived,
    })

# ---------- start with processing lock ----------------------------------
@api_view(["POST"])
@permission_classes(PERMS)
def start_anonymization(request, file_id: int):
    """
    Start anonymization for the given file while acquiring a processing lock to prevent concurrent starts.
    
    Parameters:
        file_id (int): Primary key of the file to start anonymization for.
    
    Returns:
        Response: JSON response with one of the following outcomes:
          - 200 OK: {"detail": "Anonymization started for <kind> file", "file_id": <id>, "file_type": "<kind>", "processing_locked": True}
          - 404 Not Found: {"detail": "File not found"} when the file id is unknown.
          - 409 Conflict: {"detail": "File is already being processed by another request", "file_id": <id>, "file_type": "<type>", "processing_locked": True} when a processing lock could not be acquired.
          - 500 Internal Server Error: {"detail": "Failed to start anonymization"} when the service fails to initiate processing.
    """
    # First check what type of file this is
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)
    
    file_type = info["mediaType"]
    
    # Use processing lock context to prevent duplicate processing
    with ProcessingLockContext(file_id, file_type) as lock:
        if not lock.acquired:
            return Response(
                {
                    "detail": "File is already being processed by another request",
                    "file_id": file_id,
                    "file_type": file_type,
                    "processing_locked": True
                }, 
                status=status.HTTP_409_CONFLICT
            )
        
        # Proceed with starting anonymization
        service = AnonymizationService()
        kind = service.start(file_id)
        if not kind:
            return Response({"detail": "Failed to start anonymization"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return Response({
            "detail": f"Anonymization started for {kind} file",
            "file_id": file_id,
            "file_type": kind,
            "processing_locked": True
        })


# ---------- current with coordination ------------------------------------
@api_view(['GET', 'POST', 'PUT'])
@permission_classes(DEBUG_PERMISSIONS)
def anonymization_current(request, file_id):
    """
    Set the given file as the current file for validation and return its patient data.
    
    Attempts to locate a VideoFile with the provided id first, then a RawPdfFile. If found, returns the file's patient-related data serialized with VoPPatientDataSerializer.
    
    Parameters:
        request: The HTTP request object (used to build serializer context).
        file_id: The primary key of the file to retrieve; may refer to a VideoFile or RawPdfFile.
    
    Returns:
        Response or JsonResponse: Serialized patient data when the file is found; a 404 JSON error when no file matches `file_id`; a 500 JSON error when a ValueError, TypeError, or AttributeError occurs during processing.
    """
    # Try to find the file in VideoFile first
    try:
        video_file = VideoFile.objects.select_related('sensitive_meta').get(id=file_id)
        serializer = VoPPatientDataSerializer(video_file, context={'request': request})
        return Response(serializer.data)
    except VideoFile.DoesNotExist:
        pass
    # Try to find the file in RawPdfFile
    try:
        pdf_file = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
        serializer = VoPPatientDataSerializer(pdf_file, context={'request': request})
        return Response(serializer.data)

    except RawPdfFile.DoesNotExist:
        pass

    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error in set_current_for_validation: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)

# ---------- polling coordinator info ------------------------------------
@api_view(['GET'])
@permission_classes(DEBUG_PERMISSIONS)
def polling_coordinator_info(request):
    """
    Return information about the polling coordinator's processing locks.
    
    Returns:
        Response: A DRF Response containing a dictionary with processing lock information on success,
        or a dictionary with an "error" key on failure (HTTP 500).
    """
    try:
        info = PollingCoordinator.get_processing_locks_info()
        return Response(info)
    except Exception as e:
        logger.error(f"Error getting polling coordinator info: {e}")
        return Response(
            {"error": "Failed to get coordinator info"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

# ---------- emergency lock management -----------------------------------
@api_view(['DELETE'])
@permission_classes(DEBUG_PERMISSIONS)
def clear_processing_locks(request):
    """
    Clear all processing locks, optionally filtered by file type.
    
    Parameters:
        request: The incoming HTTP request. Recognizes an optional query parameter `type`
            which, when provided, limits clearing to locks for that file type.
    
    Returns:
        Response: On success, a JSON object with:
            - `detail` (str): Confirmation message.
            - `cleared_count` (int): Number of locks that were cleared.
            - `file_type_filter` (str|None): The `type` query parameter value used to filter, or `None`.
        On failure, a 500 response with `{"error": "Failed to clear locks"}`.
    """
    try:
        file_type = request.query_params.get('type', None)
        cleared_count = PollingCoordinator.clear_all_locks(file_type)
        
        return Response({
            "detail": "Processing locks cleared",
            "cleared_count": cleared_count,
            "file_type_filter": file_type
        })
    except Exception as e:
        logger.error(f"Error clearing processing locks: {e}")
        return Response(
            {"error": "Failed to clear locks"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        
@api_view(['GET'])
@permission_classes(DEBUG_PERMISSIONS)
def has_raw_video_file(request, file_id):
    """
    Check whether a raw video file exists for the given file ID.
    
    Returns:
        Response: JSON with keys `file_id` and `has_raw_file` (`true` if a raw file exists, `false` otherwise).
    """
    exists = VideoFile.objects.filter(id=file_id, raw_file__isnull=False).exists()
    return Response({"file_id": file_id, "has_raw_file": exists})