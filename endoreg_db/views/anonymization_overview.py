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

        # 3) Combine them
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
            
            # For videos, we need to ensure the complete pipeline runs
            state = video_file.get_or_create_state()
            
            if state.anonymized:
                return JsonResponse({
                    'status': 'success', 
                    'message': 'Video is already anonymized'
                })
            
            # Check if we need to run Pipe 1 first (frame extraction + AI processing)
            if not state.frames_extracted or not state.initial_prediction_completed:
                logger.info(f"Running Pipe 1 for video {video_file.uuid}")
                
                model_name = "image_multilabel_classification_colonoscopy_default"
                success = video_file.pipe_1(
                    model_name=model_name,
                    delete_frames_after=True,
                    ocr_frame_fraction=0.01,
                    ocr_cap=5
                )
                
                if not success:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Pipe 1 processing failed'
                    }, status=500)
                
                # Simulate user validation (test_after_pipe_1) for automatic processing
                validation_success = video_file.test_after_pipe_1()
                if not validation_success:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Validation simulation failed'
                    }, status=500)
            
            # Now run Pipe 2 (anonymization)
            logger.info(f"Running Pipe 2 for video {video_file.uuid}")
            success = video_file.pipe_2()
            
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