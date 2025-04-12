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

from .paths import (
    STORAGE_DIR,
    FRAME_DIR,
    VIDEO_DIR,
    RAW_VIDEO_DIR,
    RAW_FRAME_DIR,
    TEST_RUN,
    TEST_RUN_FRAME_NUMBER,
    FRAME_PROCESSING_BATCH_SIZE,
    WEIGHTS_DIR,
    WEIGHTS_DIR_NAME,
    FRAME_DIR_NAME,
    VIDEO_DIR_NAME,
    STORAGE_DIR_NAME,
    RAW_FRAME_DIR_NAME,
    RAW_VIDEO_DIR_NAME,
    PDF_DIR_NAME,
    PDF_DIR,
    RAW_PDF_DIR,
    RAW_PDF_DIR_NAME,
)

__all__ = [
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
    "STORAGE_DIR",
    "FRAME_DIR",
    "VIDEO_DIR",
    "RAW_VIDEO_DIR",
    "RAW_FRAME_DIR",
    "TEST_RUN",
    "TEST_RUN_FRAME_NUMBER",
    "FRAME_PROCESSING_BATCH_SIZE",
    "DJANGO_NAME_SALT",
    "WEIGHTS_DIR",
    "WEIGHTS_DIR_NAME",
    "FRAME_DIR_NAME",
    "VIDEO_DIR_NAME",
    "STORAGE_DIR_NAME",
    "RAW_FRAME_DIR_NAME",
    "RAW_VIDEO_DIR_NAME",
    "RAW_PDF_DIR_NAME",
    "PDF_DIR_NAME",
    "PDF_DIR",
    "RAW_PDF_DIR",
]
