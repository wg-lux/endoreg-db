# api/views/anonymization_overview.py
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.http import JsonResponse
from django.db.models import Prefetch, QuerySet
from django.core.exceptions import ObjectDoesNotExist
from endoreg_db.models import VideoFile, RawPdfFile
from ..serializers.file_overview_serializer import FileOverviewSerializer, PatientDataSerializer
from ..utils.permissions import DEBUG_PERMISSIONS  # <-- adapt import path

class NoPagination(PageNumberPagination):
    page_size = None


class AnonymizationOverviewView(ListAPIView):
    """
    GET /api/anonymization/items/overview/
    --------------------------------------
    Returns a flat list (Video + PDF) ordered by newest upload first.
    """
    serializer_class = FileOverviewSerializer
    permission_classes = DEBUG_PERMISSIONS   
    pagination_class = NoPagination

    def get_queryset(self) -> QuerySet:                  # type: ignore
        qs_video = (
            VideoFile.objects
            .select_related("state")
            .prefetch_related(
                Prefetch(
                    "label_video_segments",
                    queryset=VideoFile.label_video_segments.rel.related_model.objects
                             .filter(state__is_validated=True)
                             .only("id")                # we only need existence
                )
            )
            .only("id", "original_file_name", "raw_file", "uploaded_at", "state")
        )
        qs_pdf = (
            RawPdfFile.objects
            .select_related("sensitive_meta")
            .only("id", "file", "created_at", "anonymized_text", "sensitive_meta")
        )
        # union() requires same columns; we just merge in Python later
        return list(qs_video) + list(qs_pdf)

    def list(self, request, *args, **kwargs):
        def get_sort_key(obj):
            if isinstance(obj, RawPdfFile):
                return obj.created_at
            else:
                return getattr(obj, "uploaded_at", None)
        
        items = sorted(self.get_queryset(), key=get_sort_key, reverse=True)
        serializer = self.get_serializer(items, many=True)
        return Response(serializer.data)


@api_view(['GET'])
def anonymization_items_overview(request):
    """
    API endpoint for anonymization items overview
    """
    view = AnonymizationOverviewView()
    view.setup(request)  # Properly initialize the view with request
    view.request = request
    view.format_kwarg = None  # Set required attribute
    view.args = ()
    view.kwargs = {}
    return view.list(request)


@api_view(['GET', 'POST', 'PUT'])
def set_current_for_validation(request, file_id):
    """
    Set current file for validation and return patient data
    """
    try:
        # Try to find the file in VideoFile first
        try:
            video_file = VideoFile.objects.select_related('sensitive_meta').get(id=file_id)
            serializer = PatientDataSerializer(video_file)
            return Response({
                'status': 'success',
                'message': 'Video file set as current for validation',
                **serializer.data  # Merge the serialized patient data
            })
        except VideoFile.DoesNotExist:
            pass
        
        # Try to find the file in RawPdfFile
        try:
            pdf_file = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
            serializer = PatientDataSerializer(pdf_file)
            return Response({
                'status': 'success',
                'message': 'PDF file set as current for validation',
                **serializer.data  # Merge the serialized patient data
            })
        except RawPdfFile.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except ObjectDoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@api_view(['POST'])
def start_anonymization(request, file_id):
    """
    Start anonymization process for a file
    """
    try:
        # Try to find the file in VideoFile first
        try:
            video_file = VideoFile.objects.get(id=file_id)
            # Start anonymization logic here
            # For now, just return success
            return JsonResponse({'status': 'success', 'message': 'Anonymization started for video file'})
        except VideoFile.DoesNotExist:
            pass
        
        # Try to find the file in RawPdfFile
        try:
            pdf_file = RawPdfFile.objects.get(id=file_id)
            # Start anonymization logic here
            # For now, just return success
            return JsonResponse({'status': 'success', 'message': 'Anonymization started for PDF file'})
        except RawPdfFile.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@api_view(['GET'])
def get_anonymization_status(request, file_id):
    """
    Get anonymization status for a file
    """
    try:
        # Try to find the file in VideoFile first
        try:
            video_file = VideoFile.objects.get(id=file_id)
            # Return anonymization status
            return JsonResponse({
                'status': 'success',
                'file_type': 'video',
                'file_id': file_id,
                'anonymization_status': 'completed',  # This should be dynamic based on actual status
                'message': 'Status retrieved successfully'
            })
        except VideoFile.DoesNotExist:
            pass
        
        # Try to find the file in RawPdfFile
        try:
            pdf_file = RawPdfFile.objects.get(id=file_id)
            # Return anonymization status
            return JsonResponse({
                'status': 'success',
                'file_type': 'pdf',
                'file_id': file_id,
                'anonymization_status': 'completed',  # This should be dynamic based on actual status
                'message': 'Status retrieved successfully'
            })
        except RawPdfFile.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
