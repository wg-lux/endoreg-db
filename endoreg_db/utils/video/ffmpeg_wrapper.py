"""Compatibility facade for FFmpeg helpers.

The implementation lives in focused sibling modules. Keep imports from this
module working for existing callers.
"""

import logging

from .command_construction import (
    TimestampRepairMode,
    _TIMESTAMP_REPAIR_SEQUENCE,
    _build_extract_frame_range_command,
    _build_extract_frames_command,
    _build_ffprobe_stream_info_command,
    _build_filter_transcode_command,
    _build_transcode_command,
    _timestamp_repair_input_args,
    _timestamp_repair_output_args,
    _update_or_append_ffmpeg_arg,
)
from .encoder_policy import (
    _build_encoder_args,
    _detect_nvenc_support,
    _get_encoder_config,
    _get_preferred_encoder,
)
from .executable_discovery import (
    _resolve_ffmpeg_executable,
    _resolve_ffprobe_executable,
    check_ffmpeg_availability,
    is_ffmpeg_available,
)
from .frame_extraction import (
    assemble_video_from_frames,
    extract_frame_range,
    extract_frames,
)
from .masking_filters import (
    _blacken_filter_args,
    _blacken_filter_args_from_normalized,
    _build_blacken_filter_expression,
    _build_blacken_filter_expression_from_normalized,
    _build_roi_mask_and_blacken_filter_expression,
    _build_roi_mask_filter_expressions,
    _normalize_blacken_intervals,
    _normalize_video_roi,
    _roi_mask_and_blacken_filter_args,
    blacken_video_frame_intervals,
    mask_video_to_roi_and_blacken_intervals,
)
from .transcode_execution import (
    FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
    _delete_partial_output,
    _run_ffmpeg_command,
    _stderr_indicates_timestamp_fault,
    _transcode_output_is_valid,
    _transcode_video_fallback,
    _transcode_video_with_timestamp_repair,
    get_stream_info,
    transcode_video,
    transcode_videofile_if_required,
)

logger = logging.getLogger("ffmpeg_wrapper")

__all__ = [
    "is_ffmpeg_available",
    "check_ffmpeg_availability",
    "get_stream_info",
    "assemble_video_from_frames",
    "transcode_video",
    "transcode_videofile_if_required",
    "blacken_video_frame_intervals",
    "mask_video_to_roi_and_blacken_intervals",
    "extract_frames",
    "extract_frame_range",
]
