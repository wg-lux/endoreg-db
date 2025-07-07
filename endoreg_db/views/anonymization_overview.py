"""
Anonymization overview API views.
Provides endpoints for managing file anonymization status and validation workflow.
"""
import logging
from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from django.db.models import Prefetch, QuerySet
from django.core.exceptions import ObjectDoesNotExist
from endoreg_db.models import VideoFile, RawPdfFile
from endoreg_db.serializers.file_overview_serializer import FileOverviewSerializer, PatientDataSerializer

# Import anonymization functionality
try:
    from endoreg_db.models.media.video.video_file_anonymize import _anonymize
except ImportError:
    _anonymize = None

logger = logging.getLogger(__name__)

# DEBUG: Remove in production
DEBUG_PERMISSIONS = [AllowAny]


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
@permission_classes(DEBUG_PERMISSIONS)
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
@permission_classes(DEBUG_PERMISSIONS)
def set_current_for_validation(request, file_id):
    """
    Set current file for validation and return patient data
    """
    try:
        # Try to find the file in VideoFile first
        try:
            video_file = VideoFile.objects.select_related('sensitive_meta').get(id=file_id)
            serializer = PatientDataSerializer(video_file, context={'request': request})
            return Response(serializer.data)
        except VideoFile.DoesNotExist:
            pass
        
        # Try to find the file in RawPdfFile
        try:
            pdf_file = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
            serializer = PatientDataSerializer(pdf_file, context={'request': request})
            return Response(serializer.data)
        except RawPdfFile.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except ObjectDoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except (ValueError, TypeError, AttributeError) as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@api_view(['POST'])
@permission_classes(DEBUG_PERMISSIONS)
def start_anonymization(request, file_id):
    """
    Start anonymization process for a file
    """
    try:
        # Try to find the file in VideoFile first
        try:
            video_file = VideoFile.objects.select_related('state', 'sensitive_meta').get(id=file_id)
            
            # Check if video anonymization is available and prerequisites are met
            if not _anonymize:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Video anonymization functionality not available'
                }, status=500)
            
            # Check video state and prerequisites
            state = video_file.get_or_create_state()
            
            if state.anonymized:
                return JsonResponse({
                    'status': 'success', 
                    'message': 'Video is already anonymized'
                })
            
            if not video_file.has_raw:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Raw video file is missing'
                }, status=400)
            
            if not state.frames_extracted:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Video frames must be extracted first'
                }, status=400)
            
            if not video_file.sensitive_meta or not video_file.sensitive_meta.is_verified:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Sensitive metadata must be validated first'
                }, status=400)
            
            # Check if all outside segments are validated
            outside_segments = video_file.get_outside_segments(only_validated=False)
            unvalidated_outside = outside_segments.filter(state__is_validated=False)
            if unvalidated_outside.exists():
                return JsonResponse({
                    'status': 'error',
                    'message': f'All outside segments must be validated first. {unvalidated_outside.count()} segments pending.'
                }, status=400)
            
            # Mark as processing and start anonymization
            logger.info(f"Starting anonymization for video {video_file.uuid}")
            
            try:
                # Try to run anonymization synchronously for now
                # In production, you might want to queue this as a background job
                success = _anonymize(video_file, delete_original_raw=True)
                
                if success:
                    return JsonResponse({
                        'status': 'success', 
                        'message': 'Video anonymization completed successfully'
                    })
                else:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Video anonymization failed'
                    }, status=500)
                    
            except Exception as e:
                logger.error(f"Error during video anonymization: {e}")
                return JsonResponse({
                    'status': 'error',
                    'message': f'Anonymization failed: {str(e)}'
                }, status=500)
                
        except VideoFile.DoesNotExist:
            pass
        
        # Try to find the file in RawPdfFile
        try:
            pdf_file = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
            
            # For PDFs, anonymization means having anonymized text
            if pdf_file.anonymized_text and pdf_file.anonymized_text.strip():
                return JsonResponse({
                    'status': 'success', 
                    'message': 'PDF is already anonymized'
                })
            
            # For now, we'll just mark it as needing manual anonymization
            # In a real implementation, you might trigger an AI service to generate anonymized text
            return JsonResponse({
                'status': 'success', 
                'message': 'PDF anonymization initiated - please complete anonymization manually'
            })
            
        except RawPdfFile.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
        
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error in start_anonymization: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@api_view(['GET'])
@permission_classes(DEBUG_PERMISSIONS)
def get_anonymization_status(request, file_id):
    """
    Get anonymization status for a file
    """
    try:
        # Try to find the file in VideoFile first
        try:
            video_file = VideoFile.objects.select_related('state', 'sensitive_meta').get(id=file_id)
            
            # Determine anonymization status based on video state
            if hasattr(video_file, 'state') and video_file.state:
                if video_file.state.anonymized:
                    anonymization_status = 'done'
                elif hasattr(video_file.state, 'processing_error') and video_file.state.processing_error:
                    anonymization_status = 'failed'
                elif video_file.state.frames_extracted:
                    anonymization_status = 'processing'
                else:
                    anonymization_status = 'not_started'
            else:
                anonymization_status = 'not_started'
            
            return JsonResponse({
                'status': 'success',
                'file_type': 'video',
                'file_id': file_id,
                'anonymizationStatus': anonymization_status,
                'message': 'Status retrieved successfully'
            })
        except VideoFile.DoesNotExist:
            pass
        
        # Try to find the file in RawPdfFile
        try:
            pdf_file = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
            
            # Determine anonymization status based on anonymized_text presence
            anonymization_status = 'done' if pdf_file.anonymized_text and pdf_file.anonymized_text.strip() else 'not_started'
            
            return JsonResponse({
                'status': 'success',
                'file_type': 'pdf',
                'file_id': file_id,
                'anonymizationStatus': anonymization_status,
                'message': 'Status retrieved successfully'
            })
        except RawPdfFile.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error in get_anonymization_status: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)