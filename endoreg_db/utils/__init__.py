"""Module for utility classes and functions."""

# --- Imports from submodules ---

# dataloader
from endoreg_db.utils.video.ffmpeg_wrapper import assemble_video_from_frames, get_stream_info, transcode_video, transcode_videofile_if_required

from .dataloader import load_model_data_from_yaml

# dates
from .dates import ensure_aware_datetime, random_day_by_month_year, random_day_by_year

# env
from .env import DEBUG, DJANGO_SETTINGS_MODULE, get_env_var

# file_operations
from .file_operations import copy_with_progress, get_uuid_filename, rename_file_uuid

# hashs
from .hashs import (
    DJANGO_NAME_SALT,
    get_examiner_hash,
    get_hash_string,
    get_patient_examination_hash,
    get_pdf_hash,
    get_video_hash,
)

# names
from .names import (
    create_mock_examiner_name,
    create_mock_patient_name,
    guess_name_gender,
)

# parse_and_generate_yaml
from .parse_and_generate_yaml import collect_center_names

# paths
from .paths import data_paths

# pydantic_models
from .pydantic_models import DbConfig
from .storage import (
    delete_field_file,
    ensure_local_file,
    save_local_file,
)
from .storage import (
    file_exists as storage_file_exists,
)

# validate_endo_roi
from .validate_endo_roi import validate_endo_roi
from .video import split_video

# ffmpeg_wrapper
from .video.ffmpeg_wrapper import (
    extract_frames,
)

# --- Exports ---

__all__ = [
    "collect_center_names",
    "copy_with_progress",
    "create_mock_examiner_name",
    "create_mock_patient_name",
    "data_paths",
    "DbConfig",
    "DEBUG",
    "DJANGO_NAME_SALT",
    "DJANGO_SETTINGS_MODULE",
    "ensure_aware_datetime",
    "get_env_var",
    "get_examiner_hash",
    "get_hash_string",
    "get_patient_examination_hash",
    "get_pdf_hash",
    "get_uuid_filename",
    "get_video_hash",
    "guess_name_gender",
    "load_model_data_from_yaml",
    "random_day_by_month_year",
    "random_day_by_year",
    "rename_file_uuid",
    "validate_endo_roi",
    "assemble_video_from_frames",  # Updated name
    "get_stream_info",
    "transcode_video",
    "transcode_videofile_if_required",  # Added
    "extract_frames",  # Added
    "split_video",
    "delete_field_file",
    "ensure_local_file",
    "save_local_file",
    "storage_file_exists",
]
