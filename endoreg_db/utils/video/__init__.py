from .video_splitter import split_video
from .names import get_video_key, identify_video_key, get_video_key_regex_by_examination_alias

# Add necessary functions from ffmpeg_wrapper
from .ffmpeg_wrapper import (
    get_stream_info,
    assemble_video_from_frames,
    transcode_video,
    transcode_videofile_if_required,
    extract_frames as ffmpeg_extract_frames # Alias to avoid potential name clash if 'extract_frames' was used elsewhere directly from __init__
)


__all__ = [
    # Keep existing
    "split_video",
    "get_video_key",
    "identify_video_key",
    "get_video_key_regex_by_examination_alias",
    # Add from ffmpeg_wrapper
    "get_stream_info",
    "assemble_video_from_frames",
    "transcode_video",
    "transcode_videofile_if_required",
    "ffmpeg_extract_frames", # Use the alias if needed
]
