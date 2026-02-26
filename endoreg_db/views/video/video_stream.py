"""
Video Streaming Views (Phase 3.2)

Separate view for streaming raw and processed video files.
Extracted from segmentation.py for better code organization.

Created: October 9, 2025
Updated: October 15, 2025 - Added HTTP 206 Range Request Support
Updated: January 23, 2026 - Added Nginx X-Accel-Redirect Support
"""

import os
import re
import logging
import mimetypes
from pathlib import Path
from typing import Tuple, Optional, Union

# Added HttpResponse for Nginx responses
from django.http import FileResponse, Http404, StreamingHttpResponse, HttpResponse
from rest_framework.views import APIView

from ...models import VideoFile
from ...utils.permissions import EnvironmentAwarePermission
from ...utils.paths import STORAGE_DIR

logger = logging.getLogger(__name__)

# Configuration for Nginx internal location
# This must match the 'location' block in your Nginx config (e.g., location /protected_media/)
NGINX_PROTECTED_URL = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")


def parse_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    """
    Parse HTTP Range header and return (start, end) byte positions.
    (Used only when serving files via Python/Django directly)
    """
    match = re.match(r"bytes=(\d+)-(\d*)", range_header)

    if not match:
        raise ValueError(f"Invalid Range header format: {range_header}")

    start = int(match.group(1))
    end_str = match.group(2)

    end = int(end_str) if end_str else file_size - 1

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
    (Used only when serving files via Python/Django directly)
    """
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1

        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)


def _get_video_path(vf: VideoFile, file_type: str) -> Path:
    """Helper to resolve the correct file path from the VideoFile model."""
    if file_type == "raw":
        if (
            hasattr(vf, "active_raw_file")
            and vf.active_raw_file
            and hasattr(vf.active_raw_file, "name")
        ):
            file_ref = vf.active_raw_file
        else:
            raise Http404("No raw video file available for this entry")

    elif file_type == "processed":
        if (
            hasattr(vf, "processed_file")
            and vf.processed_file
            and hasattr(vf.processed_file, "name")
        ):
            file_ref = vf.processed_file
        else:
            raise Http404("No processed video file available for this entry")
    else:
        raise ValueError(f"Invalid file_type: {file_type}")

    file_name = file_ref.name
    if not file_name:
        raise Http404("Video file has no storage path")

    if str(file_name).startswith("/"):
        path = Path(file_name)
    else:
        # Relative path - make absolute by prepending STORAGE_DIR
        path = STORAGE_DIR / str(file_name)
        logger.debug("Resolved relative path '%s' to absolute: %s", file_name, path)

    if not path.exists():
        raise Http404(f"Video file not found on disk: {path}")

    return path


def _serve_with_nginx(path: Path, content_type: str) -> HttpResponse | None:
    """
    Constructs an empty response with X-Accel-Redirect headers.
    Nginx intercepts this and serves the file directly from disk.
    """
    try:
        # Calculate path relative to the STORAGE_DIR to append to the Nginx internal alias
        # Example:
        #   Abs Path: /var/lib/lx-annotate/data/videos/1.mp4
        #   Storage : /var/lib/lx-annotate/data
        #   Relative: videos/1.mp4
        #   Nginx   : /protected_media/videos/1.mp4
        relative_path = path.relative_to(STORAGE_DIR)
        redirect_url = os.path.join(NGINX_PROTECTED_URL, str(relative_path))

        response = HttpResponse()
        response["Content-Type"] = content_type
        response["X-Accel-Redirect"] = redirect_url
        response["X-Accel-Buffering"] = "no"  # Crucial for video streaming

        logger.info(f"Offloading video to Nginx: {redirect_url}")
        return response

    except ValueError:
        # Fallback if path is not inside STORAGE_DIR (e.g. temp files)
        logger.warning(
            f"File {path} is not inside STORAGE_DIR {STORAGE_DIR}. Falling back to Python streaming."
        )
        return None


def _stream_video_file(
    vf: VideoFile,
    frontend_origin: str,
    file_type: str = "raw",
    range_header: Optional[str] = None,
) -> Union[FileResponse, StreamingHttpResponse, HttpResponse]:
    """
    Helper function to stream a video file.

    Logic:
    1. Checks environment variable `SERVE_WITH_NGINX`.
    2. If True: Returns headers for Nginx X-Accel-Redirect.
    3. If False: Streams file via Python with Manual Range Request support.
    """
    try:
        # 1. Resolve Path
        path = _get_video_path(vf, file_type)

        # 2. Determine MIME type
        mime, _ = mimetypes.guess_type(str(path))
        content_type = mime or "video/mp4"

        # 3. Check for Nginx Mode
        # We check specific env var, defaulting to False if not set
        serve_with_nginx = os.environ.get("SERVE_WITH_NGINX", "false").lower() == "true"

        if serve_with_nginx:
            nginx_response = _serve_with_nginx(path, content_type)
            if nginx_response:
                # Add CORS headers to the Nginx response
                nginx_response["Access-Control-Allow-Origin"] = frontend_origin
                nginx_response["Access-Control-Allow-Credentials"] = "true"
                return nginx_response

        # =========================================================
        # FALLBACK / DEV MODE: Python Streaming (Slow, High CPU)
        # =========================================================

        # Validate file size
        try:
            file_size = path.stat().st_size
            if file_size == 0:
                raise Http404("Video file is empty")
        except OSError as e:
            raise Http404(f"Cannot access video file: {str(e)}")

        # Handle Range Header (Seeking)
        if range_header:
            try:
                start, end = parse_range_header(range_header, file_size)

                response = StreamingHttpResponse(
                    stream_file_chunk(path, start, end),
                    status=206,
                    content_type=content_type,
                )
                response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                response["Content-Length"] = str(end - start + 1)
                response["Accept-Ranges"] = "bytes"
                response["Content-Disposition"] = f'inline; filename="{path.name}"'

            except ValueError as e:
                logger.warning("Invalid Range header: %s", str(e))
                response = StreamingHttpResponse(
                    status=416,
                    content_type=content_type,
                )
                response["Content-Range"] = f"bytes */{file_size}"

        else:
            # Full file stream
            try:
                file_handle = open(path, "rb")
                response = FileResponse(file_handle, content_type=content_type)
                response["Content-Length"] = str(file_size)
                response["Accept-Ranges"] = "bytes"
                response["Content-Disposition"] = f'inline; filename="{path.name}"'
            except IOError as e:
                raise Http404(f"Cannot open video file: {str(e)}")

        # CORS headers
        response["Access-Control-Allow-Origin"] = frontend_origin
        response["Access-Control-Allow-Credentials"] = "true"

        return response

    except Http404:
        raise
    except Exception as e:
        logger.error("Unexpected error in _stream_video_file: %s", str(e))
        raise Http404("Video file cannot be streamed")


class VideoStreamView(APIView):
    """
    Separate view for video streaming.

    Features:
    - Nginx X-Accel-Redirect support (Production)
    - Python Byte-Range Streaming (Development)
    - Dual file type support (raw/processed)
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request, pk=None):
        if pk is None:
            raise Http404("Video ID is required")

        try:
            # Validate ID
            try:
                video_id_int = int(pk)
            except (ValueError, TypeError):
                raise Http404("Invalid video ID format")

            # Determine file type (raw/processed)
            file_type = "raw"
            try:
                file_type_param = request.query_params.get(
                    "type"
                ) or request.query_params.get("file_type")
                if file_type_param:
                    file_type = file_type_param.lower()
                    if file_type not in ["raw", "processed"]:
                        file_type = "raw"
            except Exception:
                file_type = "raw"

            # Fetch Model
            vf = VideoFile.objects.get(pk=video_id_int)

            # Get CORS Origin
            frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")

            # Get Range Header
            range_header = request.META.get("HTTP_RANGE")

            return _stream_video_file(vf, frontend_origin, file_type, range_header)

        except VideoFile.DoesNotExist:
            raise Http404(f"Video with ID {pk} not found")
        except Http404:
            raise
        except Exception as e:
            logger.error("Unexpected error in VideoStreamView: %s", str(e))
            raise Http404("Video streaming failed")
