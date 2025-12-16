"""
Module: Video Frame Operations

This module aggregates various functionalities related to video frame management within the endoreg database system. It provides a unified interface to handle operations such as frame extraction, creation, deletion, and range management for video files.

Submodules:
    _bulk_create_frames : Handles bulk creation of frame entries.
    _create_frame_object  : Constructs a frame object from frame data.
    _delete_frames        : Performs deletion of frame records.
    _extract_frames       : Extracts frames from video content.
    _get_frame_number     : Retrieves the frame number for a specific frame.
    _get_frame_path       : Constructs the filesystem path for a frame.
    _get_frame_paths      : Retrieves paths for multiple frames.
    _get_frame_range      : Determines the range of frame indices in a video.
    _get_frame            : Obtains details for a single frame.
    _get_frames           : Aggregates multiple frame details.
    _initialize_frames    : Initializes frame data for processing.

Usage:
    Import the required functions directly from this module to perform specific video frame operations.
"""

from ._bulk_create_frames import _bulk_create_frames
from ._create_frame_object import _create_frame_object
from ._delete_frames import _delete_frames
from ._extract_frames import _extract_frames
from ._get_frame import _get_frame
from ._get_frame_number import _get_frame_number
from ._get_frame_path import _get_frame_path
from ._get_frame_paths import _get_frame_paths
from ._get_frame_range import _get_frame_range
from ._get_frames import _get_frames
from ._initialize_frames import _initialize_frames
from ._mark_frames_extracted_status import _mark_frames_extracted_status

__all__ = [
    "_bulk_create_frames",
    "_create_frame_object",
    "_delete_frames",
    "_extract_frames",
    "_get_frame_number",
    "_get_frame_path",
    "_get_frame_paths",
    "_get_frame_range",
    "_get_frame",
    "_get_frames",
    "_initialize_frames",
    "_mark_frames_extracted_status",
]
