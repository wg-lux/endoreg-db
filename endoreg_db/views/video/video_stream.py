"""
Video Streaming Views (Phase 3.2)

Separate view for streaming raw and processed video files.
Extracted from segmentation.py for better code organization.

Created: October 9, 2025
"""

from pathlib import Path
import os
import mimetypes
import logging
from typing import cast
from django.http import FileResponse, Http404
from rest_framework.views import APIView
from rest_framework.request import Request

from ...models import VideoFile
from ...utils.permissions import EnvironmentAwarePermission
from ...utils.paths import STORAGE_DIR  # Import STORAGE_DIR for path resolution

logger = logging.getLogger(__name__)


def _stream_video_file(vf: VideoFile, frontend_origin: str, file_type: str = 'raw') -> FileResponse:
    """
    Helper function to stream a video file with proper headers and CORS support.
    
    Args:
        vf: VideoFile model instance
        frontend_origin: Frontend origin URL for CORS headers
        file_type: Either 'raw' (original video) or 'processed' (anonymized video)
        
    Returns:
        FileResponse: HTTP response streaming the video file
        
    Raises:
        Http404: If video file not found or cannot be accessed
        
    Note:
        Permissions are handled by the calling view, not in this helper function.
    """
    try:
        # Determine which file to stream based on file_type
        if file_type == 'raw':
            if hasattr(vf, 'active_raw_file') and vf.active_raw_file and hasattr(vf.active_raw_file, 'name'):
                file_ref = vf.active_raw_file
            else:
                raise Http404("No raw video file available for this entry")
            
        elif file_type == 'processed':
            if hasattr(vf, 'processed_file') and vf.processed_file and hasattr(vf.processed_file, 'name'):
                file_ref = vf.processed_file
            else:
                raise Http404("No processed video file available for this entry")
        else:
            raise ValueError(f"Invalid file_type: {file_type}. Must be 'raw' or 'processed'.")
        
        # FIX: Handle both relative and absolute paths
        # Django FileField.path returns .name if MEDIA_ROOT is not set
        # Import services store relative paths like "videos/UUID.mp4"
        # We need to resolve to absolute path: STORAGE_DIR / "videos/UUID.mp4"
        file_name = file_ref.name
        
        if file_name.startswith('/'):
            # Already absolute path
            path = Path(file_name)
        else:
            # Relative path - make absolute by prepending STORAGE_DIR
            path = STORAGE_DIR / file_name
            logger.debug(f"Resolved relative path '{file_name}' to absolute: {path}")
        
        # Validate file exists on disk
        if not path.exists():
            raise Http404(f"Video file not found on disk: {path}")

        # Validate file size before streaming
        try:
            file_size = path.stat().st_size
            if file_size == 0:
                raise Http404("Video file is empty")
        except OSError as e:
            raise Http404(f"Cannot access video file: {str(e)}")

        # Determine MIME type
        mime, _ = mimetypes.guess_type(str(path))
        content_type = mime or 'video/mp4'  # Default to mp4 if detection fails
        
        try:
            # Open file in binary mode - FileResponse will handle closing
            file_handle = open(path, 'rb')
            response = FileResponse(file_handle, content_type=content_type)
            
            # Set HTTP headers for video streaming
            response['Content-Length'] = str(file_size)
            response['Accept-Ranges'] = 'bytes'  # Enable HTTP range requests for seeking
            response['Content-Disposition'] = f'inline; filename="{path.name}"'
            
            # CORS headers for frontend access
            response["Access-Control-Allow-Origin"] = frontend_origin
            response["Access-Control-Allow-Credentials"] = "true"
            
            return response
            
        except IOError as e:
            raise Http404(f"Cannot open video file: {str(e)}")
            
    except Exception as e:
        # Log unexpected errors but don't expose internal details
        logger.error(f"Unexpected error in _stream_video_file: {str(e)}")
        raise Http404("Video file cannot be streamed")


class VideoStreamView(APIView):
    """
    Separate view for video streaming to avoid DRF content negotiation issues.
    
    Supports streaming both raw (original) and processed (anonymized) videos.
    
    Query Parameters:
        type: 'raw' (default) or 'processed' - Selects which video file to stream
        file_type: (deprecated, use 'type') - Legacy parameter for backwards compatibility
        
    Examples:
        GET /api/media/videos/1/?type=raw - Stream original raw video
        GET /api/media/videos/1/?type=processed - Stream anonymized/masked video
        GET /api/videostream/1/ - Default to raw video (legacy endpoint)
        
    Phase 3.2 Implementation:
        - Supports dual video comparison (raw vs processed)
        - Backward compatible with legacy ?file_type= parameter
        - Proper error handling with Http404
        - CORS support for frontend access
        - HTTP range support for video seeking
    """
    permission_classes = [EnvironmentAwarePermission]
    
    def get(self, request, pk=None):
        """
        Stream raw or anonymized video file with HTTP range and CORS support.
        
        Args:
            request: HTTP request object
            pk: Video ID (primary key)
            
        Returns:
            FileResponse: Streaming video file
            
        Raises:
            Http404: If video not found or file cannot be accessed
        """
        if pk is None:
            raise Http404("Video ID is required")
            
        # Initialize variables in outer scope
        video_id_int = None
        
        try:
            # Validate video_id is numeric
            try:
                video_id_int = int(pk)
            except (ValueError, TypeError):
                raise Http404("Invalid video ID format")
            
            # Support both 'type' (frontend standard) and 'file_type' (legacy)
            # Priority: type > file_type > default 'raw'
            file_type = 'raw'  # Default value
            try:
                file_type_param = request.query_params.get('type') or request.query_params.get('file_type')
                if file_type_param:
                    file_type = file_type_param.lower()
                    
                    if file_type not in ['raw', 'processed']:
                        logger.warning(f"Invalid file_type '{file_type}', defaulting to 'raw'")
                        file_type = 'raw'
                    
            except Exception as e:
                logger.warning(f"Error parsing file_type parameter: {e}, defaulting to 'raw'")
                file_type = 'raw'
                            
            # Fetch video from database
            vf = VideoFile.objects.get(pk=video_id_int)
            
            # Get frontend origin for CORS
            frontend_origin = os.environ.get('FRONTEND_ORIGIN', 'http://localhost:8000')
            
            # Stream the video file
            return _stream_video_file(vf, frontend_origin, file_type)
            
        except VideoFile.DoesNotExist:
            raise Http404(f"Video with ID {pk} not found")
            
        except Http404:
            # Re-raise Http404 exceptions as they should bubble up
            raise
            
        except Exception as e:
            # Log unexpected errors and convert to Http404
            logger.error(f"Unexpected error in VideoStreamView for video_id={pk}: {str(e)}")
            raise Http404("Video streaming failed")
