"""Module for utility classes and functions."""

from .dataloader import load_model_data_from_yaml
from .parse_and_generate_yaml import collect_center_names
from .validate_endo_roi import validate_endo_roi
from .pydantic_models import DbConfig
from .dates import random_day_by_month_year, random_day_by_year, ensure_aware_datetime
from .hashs import (
    get_hash_string,
    get_pdf_hash,
    get_examiner_hash,
    get_video_hash,
    get_patient_examination_hash,
    DJANGO_NAME_SALT
)
from .names import (
    create_mock_patient_name,
    create_mock_examiner_name,
    guess_name_gender,
)

from .file_operations import (
    get_uuid_filename,
    rename_file_uuid,
    copy_with_progress

)

from .paths import data_paths

from .env import (
    get_env_var,
    DEBUG,
    DJANGO_SETTINGS_MODULE
)

__all__ = [

    "get_uuid_filename",
    "rename_file_uuid",
    "copy_with_progress",

    "load_model_data_from_yaml",
    "collect_center_names",
    "validate_endo_roi",
    "get_examiner_hash",
    "DbConfig",
    "random_day_by_month_year",
    "random_day_by_year",
    "ensure_aware_datetime",
    "get_hash_string",
    "create_mock_examiner_name",
    "create_mock_patient_name",
    "guess_name_gender",
    "get_pdf_hash",
    "get_video_hash",
    "get_patient_examination_hash",
    "DJANGO_NAME_SALT",
    "data_paths",
    "get_env_var",
    "DEBUG",
    "DJANGO_SETTINGS_MODULE"
]

