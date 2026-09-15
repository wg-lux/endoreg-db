"""Convenience exports for common utility helpers."""

from typing import TYPE_CHECKING

# parse_and_generate_yaml
from . import dataloader as dataloader

# file_operations
from . import file_operations as file_operations
from . import paths as paths

# dates
from .dates import (
    ensure_aware_datetime,
    random_day_by_month_year,
    random_day_by_year,
)

# env
from .env import DEBUG, get_env_var
from .file_operations import (
    copy_with_progress,
    get_content_hash_filename,
)

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
from .parse_and_generate_yaml import collect_center_names

# paths
from .paths import data_paths

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
from .validate_endo_roi import validate_endo_roi
from .yaml_model_loader import load_model_data_from_yaml

# --- Lazy-loaded Video Utilities with Type Safety ---

if TYPE_CHECKING:
    # The type checker pulls the exact signatures from the wrapper package here
    from endoreg_db.utils.ffmpeg_wrapper import (
        assemble_video_from_frames as assemble_video_from_frames,
    )
    from endoreg_db.utils.ffmpeg_wrapper import (
        extract_frames as extract_frames,
    )
    from endoreg_db.utils.ffmpeg_wrapper import (
        get_stream_info as get_stream_info,
    )
    from endoreg_db.utils.ffmpeg_wrapper import (
        transcode_video as transcode_video,
    )
    from endoreg_db.utils.ffmpeg_wrapper import (
        transcode_videofile_if_required as transcode_videofile_if_required,
    )
else:
    # The runtime engine uses these deferred implementations to preserve speed
    def assemble_video_from_frames(*args, **kwargs):
        from endoreg_db.utils.ffmpeg_wrapper import (
            assemble_video_from_frames as _impl,
        )

        return _impl(*args, **kwargs)

    def get_stream_info(*args, **kwargs):
        from endoreg_db.utils.ffmpeg_wrapper import get_stream_info as _impl

        return _impl(*args, **kwargs)

    def transcode_video(*args, **kwargs):
        from endoreg_db.utils.ffmpeg_wrapper import transcode_video as _impl

        return _impl(*args, **kwargs)

    def transcode_videofile_if_required(*args, **kwargs):
        from endoreg_db.utils.ffmpeg_wrapper import (
            transcode_videofile_if_required as _impl,
        )

        return _impl(*args, **kwargs)

    def extract_frames(*args, **kwargs):
        from endoreg_db.utils.ffmpeg_wrapper import extract_frames as _impl

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
    "assemble_video_from_frames",
    "get_stream_info",
    "transcode_video",
    "transcode_videofile_if_required",
    "extract_frames",
    "delete_field_file",
    "ensure_local_file",
    "field_file_is_readable",
    "file_operations",
    "paths",
    "save_local_file",
    "storage_file_exists",
]
