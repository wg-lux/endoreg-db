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
from endoreg_db.services.video_import import import_and_anonymize
from endoreg_db.utils.permissions import DEBUG_PERMISSIONS

logger = logging.getLogger(__name__)


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



@api_view(['GET', 'POST', 'PUT'])
@permission_classes(DEBUG_PERMISSIONS)
def set_current_for_validation(request, file_id):
    """
    Set current file for validation and return patient data
    """
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
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error in set_current_for_validation: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)


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
            
            s = video_file.state

            if video_file.sensitive_meta:
                s.sensitive_meta_processed = True
            
                
            
            # Determine anonymization status based on video state
            if s:
                # ---- finished states ----------------------------------------
                if s.anonymization_validated:
                    anonymization_status = 'validated'
                elif s.sensitive_meta_processed:
                    anonymization_status = 'done'
                # ---- still running ------------------------------------------
                elif s.frames_extracted and not s.anonymized:
                    anonymization_status = 'processing_anonymization'
                elif s.was_created and not s.frames_extracted:
                    anonymization_status = 'extracting_frames'  # 🐛 fixed typo
                elif getattr(s, "processing_error", False):
                    anonymization_status = 'failed'
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
    
    
@api_view(['POST'])
@permission_classes(DEBUG_PERMISSIONS)
def start_anonymization(request, file_id):
    """
    Start anonymization for a file
    """
    try:
        # Try to find the file in VideoFile first
        try:
            video_file = VideoFile.objects.select_related('state', 'sensitive_meta').get(id=file_id)
            import_and_anonymize(video_file.get_raw_file_path, video_file.center, video_file.processor)
            
            return JsonResponse({'status': 'success', 'message': 'Anonymization started for video file'})
        except Exception as e:
            pass
        
        # Try to find the file in RawPdfFile
        try:
            pdf_file = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
            pdf_file.sensitive_meta.anonymization_started = True  # Mark as anonymization started
            pdf_file.sensitive_meta.save()
            return JsonResponse({'status': 'success', 'message': 'Anonymization started for PDF file'})
        except RawPdfFile.DoesNotExist:
            pass
        
        return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Error in start_anonymization: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
@api_view(['POST'])
@permission_classes(DEBUG_PERMISSIONS)
def validate_anonymization(request, file_id):
    """
    Validate anonymization for a file
    """
    if request:
        try:
            # Try to find the file in VideoFile first
            try:
                video_file = VideoFile.objects.select_related('state', 'sensitive_meta').get(id=file_id)
                video_file.state.anonymization_validated = True
                video_file.state.save()
                return JsonResponse({'status': 'success', 'message': 'Anonymization validated for video file'})
            except VideoFile.DoesNotExist:
                pass
            
            # Try to find the file in RawPdfFile
            try:
                pdf_file = RawPdfFile.objects.select_related('sensitive_meta').get(id=file_id)
                pdf_file.sensitive_meta.anonymization_validated = True
                pdf_file.sensitive_meta.save()
                return JsonResponse({'status': 'success', 'message': 'Anonymization validated for PDF file'})
            except RawPdfFile.DoesNotExist:
                pass
            
            return JsonResponse({'status': 'error', 'message': 'File not found'}, status=404)
        except (ValueError, TypeError, AttributeError) as e:
            logger.error(f"Error in validate_anonymization: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)