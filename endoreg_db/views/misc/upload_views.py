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
        """
        Placeholder no-op used when the asynchronous task runner is unavailable.
        
        Parameters:
            job_id (UUID or str): Identifier of the UploadJob that would be processed; this function does nothing with it.
        """
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
        Handle an uploaded file: validate input, create an UploadJob, and enqueue or mark it for processing.
        
        Performs the following observable actions:
        - Validates presence, non-emptiness, filename, size (against MAX_FILE_SIZE), and MIME type (via _detect_mime_type and ALLOWED_MIME_TYPES).
        - Creates an UploadJob with the uploaded file and detected content_type.
        - If Celery is available, attempts to start asynchronous processing; on failure marks the job as failed.
        - If Celery is not available, marks the job as processing (development fallback).
        - Returns a status URL for polling the job status.
        
        Returns:
            Response: On success returns a 201 response with JSON containing:
                - 'upload_id' (str): the created job UUID as a string,
                - 'status_url' (str): URL to query upload status,
                - 'message' (str): success message.
            On client validation failure returns 400 with {'error': <message>}.
            On server-side failure returns 500 with {'error': <message>}.
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
        Determine the MIME type of an uploaded file from its content, filename, or extension and return it as a string.
        
        Attempts detection using file content and filename heuristics and falls back to common extension mappings. Ensures the uploaded file's read pointer is reset to the start before returning.
        
        Parameters:
            uploaded_file (file-like): An object with a .read(), .seek(), and .name attribute (e.g., Django UploadedFile).
        
        Returns:
            str: The detected MIME type (for example, 'application/pdf' or 'video/mp4').
        
        Raises:
            ValueError: If the MIME type cannot be determined.
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
        Retrieve the current status of an upload job.
        
        Parameters:
            id (str | UUID): UUID of the UploadJob to retrieve.
        
        Returns:
            dict: Serialized upload job status as produced by UploadJobStatusSerializer.
        
        Raises:
            Http404: If no UploadJob with the given id exists.
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