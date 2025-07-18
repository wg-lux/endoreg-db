import mimetypes
from django.http import Http404
from django.urls import reverse
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

# Try to import python-magic, but provide fallback if not available
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False

from endoreg_db.models.upload_job import UploadJob
from endoreg_db.serializers.misc.upload_job import (
    UploadJobStatusSerializer,
)

# Try to import celery task, but provide fallback
try:
    from endoreg_db.tasks.upload_tasks import process_upload_job
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    # Define a dummy function for development
    def process_upload_job(job_id):
        pass


@method_decorator(csrf_exempt, name='dispatch')
class UploadFileView(APIView):
    """
    Handle file uploads (POST /api/upload/).
    
    Accepts multipart/form-data with a 'file' field containing PDF or video files.
    Creates an UploadJob and starts asynchronous processing.
    
    Returns:
        201 Created: {"upload_id": "<uuid>", "status_url": "/api/upload/<uuid>/status/"}
        400 Bad Request: File validation errors
    """
    
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [AllowAny]  # Adjust based on your auth requirements
    
    # Maximum file size (1 GiB)
    MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB in bytes
    
    # Allowed MIME types
    ALLOWED_MIME_TYPES = {
        'application/pdf',
        'video/mp4',
        'video/avi', 
        'video/quicktime',
        'video/x-msvideo',
        'video/x-ms-wmv'
    }

    def post(self, request, *args, **kwargs):
        """
        Handle file upload and create processing job.
        """
        # Validate file presence
        if 'file' not in request.FILES:
            return Response(
                {'error': 'No file provided. Please include a file in the "file" field.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        uploaded_file = request.FILES['file']
        
        # Validate file is not empty
        if not uploaded_file or uploaded_file.size == 0:
            return Response(
                {'error': 'Uploaded file is empty. Please select a valid file.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate file size
        if uploaded_file.size > self.MAX_FILE_SIZE:
            return Response(
                {'error': f'File too large. Maximum size is {self.MAX_FILE_SIZE // (1024**3)} GB.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate filename
        if not uploaded_file.name or uploaded_file.name.strip() == '':
            return Response(
                {'error': 'Invalid filename. Please ensure the file has a valid name.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Detect MIME type
        try:
            content_type = self._detect_mime_type(uploaded_file)
        except Exception as e:
            return Response(
                {'error': f'Could not determine file type: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate MIME type
        if content_type not in self.ALLOWED_MIME_TYPES:
            return Response(
                {'error': f'Unsupported file type: {content_type}. Allowed types: PDF, MP4, AVI, MOV, WMV.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Create upload job
            upload_job = UploadJob.objects.create(
                file=uploaded_file,
                content_type=content_type
            )
            
            # Start asynchronous processing if Celery is available
            if CELERY_AVAILABLE:
                try:
                    process_upload_job.delay(str(upload_job.id))
                except Exception as e:
                    # If Celery task fails to start, mark job as failed
                    upload_job.mark_failed(f'Failed to start processing: {str(e)}')
                    return Response(
                        {'error': f'Failed to start processing: {str(e)}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            else:
                # For development without Celery, mark as processing immediately
                upload_job.mark_processing()
                # In production, this would be handled by Celery
                # For now, just leave it in processing state
            
            # Prepare response
            status_url = reverse('upload_status', kwargs={'id': upload_job.id})
            response_data = {
                'upload_id': str(upload_job.id),  # Ensure UUID is converted to string
                'status_url': status_url,
                'message': 'Upload job created successfully'
            }
            
            # Return the response data directly since serializer fields are read-only
            return Response(
                response_data,
                status=status.HTTP_201_CREATED
            )
            
        except Exception as e:
            return Response(
                {'error': f'Failed to create upload job: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _detect_mime_type(self, uploaded_file) -> str:
        """
        Detect MIME type using python-magic as primary method,
        fallback to mimetypes module.
        """
        try:
            # Reset file pointer
            uploaded_file.seek(0)
            
            # Try python-magic first (more reliable) if available
            if MAGIC_AVAILABLE:
                try:
                    # Read first chunk for magic detection
                    chunk = uploaded_file.read(2048)
                    uploaded_file.seek(0)  # Reset again
                    
                    mime_type = magic.from_buffer(chunk, mime=True)
                    if mime_type and mime_type != 'application/octet-stream':
                        return mime_type
                except Exception:
                    pass  # Fall back to mimetypes
            
            # Fallback to mimetypes module
            mime_type, _ = mimetypes.guess_type(uploaded_file.name)
            if mime_type:
                return mime_type
            
            # Last resort - check file extension
            if uploaded_file.name.lower().endswith('.pdf'):
                return 'application/pdf'
            elif uploaded_file.name.lower().endswith(('.mp4', '.m4v')):
                return 'video/mp4'
            elif uploaded_file.name.lower().endswith('.avi'):
                return 'video/avi'
            elif uploaded_file.name.lower().endswith(('.mov', '.qt')):
                return 'video/quicktime'
            elif uploaded_file.name.lower().endswith('.wmv'):
                return 'video/x-ms-wmv'
            
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
    
    permission_classes = [AllowAny]  # Adjust based on your auth requirements

    def get(self, request, id, *args, **kwargs):
        """
        Return the current status of an upload job.
        """
        try:
            # Look up upload job by UUID
            upload_job = UploadJob.objects.select_related('sensitive_meta').get(id=id)
            
            # Serialize the response
            serializer = UploadJobStatusSerializer(upload_job)
            
            return Response(
                serializer.data,
                status=status.HTTP_200_OK
            )
            
        except UploadJob.DoesNotExist:
            raise Http404("Upload job not found")
        except Exception as e:
            return Response(
                {'error': f'Failed to get upload status: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )