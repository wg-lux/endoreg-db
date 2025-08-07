from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.services.anonymization import AnonymizationService
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from django.db.models import Prefetch, QuerySet
from django.core.exceptions import ObjectDoesNotExist
from endoreg_db.models import VideoFile, RawPdfFile
from endoreg_db.serializers.misc.file_overview import FileOverviewSerializer
from endoreg_db.serializers.misc.vop_patient_data import VoPPatientDataSerializer
from django.http import JsonResponse
import logging

logger = logging.getLogger(__name__)
PERMS = [EnvironmentAwarePermission]

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
    permission_classes = [EnvironmentAwarePermission]
    pagination_class = NoPagination

    def get_queryset(self):
        """
        Returns a combined queryset of VideoFile and RawPdfFile instances.
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
            .only("id", "file", "created_at", 
                "text", "anonymized_text",       # These fields only exist on RawPdfFile
                "sensitive_meta")
        )

        return list(qs_video) + list(qs_pdf)

# Keep the legacy function-based view for backward compatibility
@api_view(["GET"])
@permission_classes(PERMS)
def anonymization_overview(request):
    data = AnonymizationService.list_items()
    return Response(data)

# ---------- status ------------------------------------------------------
@api_view(["GET"])
@permission_classes(PERMS)
def anonymization_status(request, file_id: int):
    info = AnonymizationService.get_status(file_id)
    if not info:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        "file_id": file_id,
        "file_type": info["type"],
        "anonymizationStatus": info["status"],
    })

# ---------- start -------------------------------------------------------
@api_view(["POST"])
@permission_classes(PERMS)
def start_anonymization(request, file_id: int):
    kind = AnonymizationService.start(file_id)
    if not kind:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"detail": f"Anonymization started for {kind} file"})

# ---------- validate ----------------------------------------------------
@api_view(["POST"])
@permission_classes(PERMS)
def validate_anonymization(request, file_id: int):
    kind = AnonymizationService.validate(file_id)
    if not kind:
        return Response({"detail": "File not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response({"detail": f"Anonymization validated for {kind} file"})

@api_view(['GET', 'POST', 'PUT'])
@permission_classes(PERMS)
def anonymization_current(request, file_id):
    """
    Set current file for validation and return patient data
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
        logger.error(f"Error in anonymization_current: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)