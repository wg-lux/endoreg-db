"""Convenience exports for common utility helpers."""

from .data_loading.yaml_model_loader import load_model_data_from_yaml

# dates
from .core.dates import (
    ensure_aware_datetime,
    random_day_by_month_year,
    random_day_by_year,
)

# env
from .core.env import DEBUG, get_env_var

# file_operations
from .filesystem import file_operations as file_operations
from .filesystem import paths as paths
from .filesystem.file_operations import (
    copy_with_progress,
    get_content_hash_filename,
)

# hashs
from .security.hashs import (
    DJANGO_NAME_SALT,
    get_examiner_hash,
    get_hash_string,
    get_patient_examination_hash,
    get_pdf_hash,
    get_video_hash,
)

# names
from .core.names import (
    create_mock_examiner_name,
    create_mock_patient_name,
    guess_name_gender,
)

# parse_and_generate_yaml
from .data_loading import dataloader as dataloader
from .data_loading.parse_and_generate_yaml import collect_center_names

# paths
from .filesystem.paths import data_paths

# pydantic_models
from .pydantic_models import DbConfig
from .storage import (
    delete_field_file,
    ensure_local_file,
    field_file_is_readable,
    save_local_file,
)
from .storage import file_exists as storage_file_exists

# validate_endo_roi
from .validation.endo_roi import validate_endo_roi


def assemble_video_from_frames(*args, **kwargs):
    from endoreg_db.utils.video.ffmpeg_wrapper import (
        assemble_video_from_frames as _impl,
    )

    return _impl(*args, **kwargs)


def get_stream_info(*args, **kwargs):
    from endoreg_db.utils.video.ffmpeg_wrapper import get_stream_info as _impl

    return _impl(*args, **kwargs)


def transcode_video(*args, **kwargs):
    from endoreg_db.utils.video.ffmpeg_wrapper import transcode_video as _impl

    return _impl(*args, **kwargs)


def transcode_videofile_if_required(*args, **kwargs):
    from endoreg_db.utils.video.ffmpeg_wrapper import (
        transcode_videofile_if_required as _impl,
    )

    return _impl(*args, **kwargs)


def extract_frames(*args, **kwargs):
    from endoreg_db.utils.video.ffmpeg_wrapper import extract_frames as _impl

    return _impl(*args, **kwargs)


# --- Exports ---

__all__ = [
    "collect_center_names",
    "copy_with_progress",
    "create_mock_examiner_name",
    "create_mock_patient_name",
    "data_paths",
    "dataloader",
    "DbConfig",
    "DEBUG",
    "DJANGO_NAME_SALT",
    "ensure_aware_datetime",
    "get_env_var",
    "get_examiner_hash",
    "get_hash_string",
    "get_patient_examination_hash",
    "get_pdf_hash",
    "get_content_hash_filename",
    "get_video_hash",
    "guess_name_gender",
    "load_model_data_from_yaml",
    "random_day_by_month_year",
    "random_day_by_year",
    "validate_endo_roi",
    "assemble_video_from_frames",  # Updated name
    "get_stream_info",
    "transcode_video",
    "transcode_videofile_if_required",  # Added
    "extract_frames",  # Added
    "delete_field_file",
    "ensure_local_file",
    "field_file_is_readable",
    "file_operations",
    "paths",
    "save_local_file",
    "storage_file_exists",
]
