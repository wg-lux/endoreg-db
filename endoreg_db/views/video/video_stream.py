"""
Video Streaming Views (Phase 3.2)

Separate view for streaming raw and processed video files.
Extracted from segmentation.py for better code organization.

Created: October 9, 2025
Updated: October 15, 2025 - Added HTTP 206 Range Request Support
"""

import os
import re
import logging
import mimetypes
from pathlib import Path
from typing import Tuple, Optional
from django.http import FileResponse, Http404, StreamingHttpResponse
from rest_framework.views import APIView

from ...models import VideoFile
from ...utils.permissions import EnvironmentAwarePermission
from ...utils.paths import STORAGE_DIR  # Import STORAGE_DIR for path resolution

logger = logging.getLogger(__name__)


def parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    """
    Parse HTTP Range header and return (start, end) byte positions.
    
    Args:
        range_header: HTTP Range header value (e.g., "bytes=0-1023")
        file_size: Total file size in bytes
        
    Returns:
        Tuple of (start_byte, end_byte) inclusive
        
    Raises:
        ValueError: If range header is invalid
    """
    # Expected format: "bytes=start-end" or "bytes=start-"
    match = re.match(r'bytes=(\d+)-(\d*)', range_header)
    
    if not match:
        raise ValueError(f"Invalid Range header format: {range_header}")
    
    start = int(match.group(1))
    end_str = match.group(2)
    
    # If end is not specified, use file size - 1
    end = int(end_str) if end_str else file_size - 1
    
    # Validate range
    if start >= file_size or start < 0:
        raise ValueError(f"Start byte {start} is out of range (file size: {file_size})")
    
    if end >= file_size:
        end = file_size - 1
    
    if start > end:
        raise ValueError(f"Invalid range: start ({start}) > end ({end})")
    
    return start, end


def stream_file_chunk(file_path: Path, start: int, end: int, chunk_size: int = 8192):
    """
    Generator that yields chunks of a file within the specified byte range.
    
    Args:
        file_path: Path to the file
        start: Start byte position (inclusive)
        end: End byte position (inclusive)
        chunk_size: Size of each chunk to yield
        
    Yields:
        Bytes chunks from the file
    """
    with open(file_path, 'rb') as f:
        f.seek(start)
        remaining = end - start + 1  # +1 because end is inclusive
        
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)


def _stream_video_file(vf: VideoFile, frontend_origin: str, file_type: str = 'raw', range_header: Optional[str] = None) -> FileResponse | StreamingHttpResponse:
    """
    Helper function to stream a video file with proper headers, CORS support, and HTTP Range Requests.
    
    Args:
        vf: VideoFile model instance
        frontend_origin: Frontend origin URL for CORS headers
        file_type: Either 'raw' (original video) or 'processed' (anonymized video)
        range_header: HTTP Range header value (e.g., "bytes=0-1023") for partial content requests
        
    Returns:
        FileResponse: HTTP 200 response streaming the entire file (no range header)
        StreamingHttpResponse: HTTP 206 response streaming partial content (with range header)
        
    Raises:
        Http404: If video file not found or cannot be accessed
        
    Note:
        Permissions are handled by the calling view, not in this helper function.
        HTTP 206 Partial Content support is critical for video seeking in browsers.
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
            logger.debug("Resolved relative path '%s' to absolute: %s", file_name, path)
        
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
        
        # ✅ NEW: HTTP Range Request support for video seeking
        if range_header:
            try:
                # Parse Range header
                start, end = parse_range_header(range_header, file_size)
                logger.debug("Range request: bytes=%d-%d (total: %d)", start, end, file_size)
                
                # Stream partial content (HTTP 206)
                response = StreamingHttpResponse(
                    stream_file_chunk(path, start, end),
                    status=206,  # Partial Content
                    content_type=content_type
                )
                
                # Set Range-specific headers
                response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
                response['Content-Length'] = str(end - start + 1)
                response['Accept-Ranges'] = 'bytes'
                response['Content-Disposition'] = f'inline; filename="{path.name}"'
                
            except ValueError as e:
                # Invalid range header - return 416 Range Not Satisfiable
                logger.warning("Invalid Range header: %s", str(e))
                response = StreamingHttpResponse(
                    status=416,  # Range Not Satisfiable
                    content_type=content_type
                )
                response['Content-Range'] = f'bytes */{file_size}'
                
        else:
            # No Range header - stream entire file (HTTP 200)
            try:
                # Open file in binary mode - FileResponse will handle closing
                file_handle = open(path, 'rb')
                response = FileResponse(file_handle, content_type=content_type)
                
                # Set HTTP headers for video streaming
                response['Content-Length'] = str(file_size)
                response['Accept-Ranges'] = 'bytes'  # Enable HTTP range requests for seeking
                response['Content-Disposition'] = f'inline; filename="{path.name}"'
                
            except IOError as e:
                raise Http404(f"Cannot open video file: {str(e)}")
        
        # CORS headers for frontend access (both HTTP 200 and 206)
        response["Access-Control-Allow-Origin"] = frontend_origin
        response["Access-Control-Allow-Credentials"] = "true"
        
        return response
            
    except Exception as e:
        # Log unexpected errors but don't expose internal details
        logger.error("Unexpected error in _stream_video_file: %s", str(e))
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
        Stream raw or anonymized video file with HTTP Range Request and CORS support.
        
        Supports HTTP 206 Partial Content for video seeking functionality.
        
        Args:
            request: HTTP request object
            pk: Video ID (primary key)
            
        Returns:
            FileResponse: HTTP 200 streaming entire video file (no range header)
            StreamingHttpResponse: HTTP 206 streaming partial content (with range header)
            
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
                        logger.warning("Invalid file_type '%s', defaulting to 'raw'", file_type)
                        file_type = 'raw'
                    
            except Exception as e:
                logger.warning("Error parsing file_type parameter: %s, defaulting to 'raw'", e)
                file_type = 'raw'
                            
            # Fetch video from database
            vf = VideoFile.objects.get(pk=video_id_int)
            
            # Get frontend origin for CORS
            frontend_origin = os.environ.get('FRONTEND_ORIGIN', 'http://localhost:8000')
            
            # ✅ NEW: Extract Range header for HTTP 206 support
            range_header = request.META.get('HTTP_RANGE')
            
            # Stream the video file with optional range support
            return _stream_video_file(vf, frontend_origin, file_type, range_header)
            
        except VideoFile.DoesNotExist:
            raise Http404(f"Video with ID {pk} not found")
            
        except Http404:
            # Re-raise Http404 exceptions as they should bubble up
            raise
            
        except Exception as e:
            # Log unexpected errors and convert to Http404
            logger.error("Unexpected error in VideoStreamView for video_id=%s: %s", pk, str(e))
            raise Http404("Video streaming failed")
