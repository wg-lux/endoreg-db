from __future__ import annotations

import random
from datetime import date
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol, TypeAlias, cast

from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models.fields.files import FieldFile

from endoreg_db.models import (
    AiModel,
    Center,
    EndoscopyProcessor,
    Examination,
    ExaminationIndication,
    Gender,
    InformationSource,
    ModelMeta,
    Patient,
)
from endoreg_db.services.raw_pdf_files import (
    create_initialized_raw_pdf_file_from_path,
    process_raw_pdf_file,
)
from endoreg_db.utils import create_mock_patient_name
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    safe_unlink_file,
    sha256_file,
)

from .model_weights import ensure_managed_stub_weights

if TYPE_CHECKING:
    from endoreg_db.services.raw_pdf_files.metadata import ReportMetaJsonObject


logger = getLogger("default_objects")


DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
DEFAULT_ENDOSCOPE_NAME = "test_endoscope"
DEFAULT_ENDOSCOPY_PROCESSOR_NAME = "olympus_cv_1500"

DEFAULT_EGD_PATH = Path("tests/assets/lux-gastro-report.pdf")
DEFAULT_GENDERS = ["male", "female"]
DEFAULT_EXAMINATIONS = ["colonoscopy"]
DEFAULT_INDICATIONS = [
    "colonoscopy",
    "colonoscopy_screening",
    "colonoscopy_lesion_removal_small",
    "colonoscopy_lesion_removal_emr",
    "colonoscopy_lesion_removal_large",
    "colonoscopy_diagnostic_acute_symptomatic",
]

DEFAULT_SEGMENTATION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"

DEFAULT_GENDER = "unknown"
DEFAULT_PATIENT_FIRST_NAME = "TestFirst"
DEFAULT_PATIENT_LAST_NAME = "TestLast"
DEFAULT_PATIENT_GENDER_NAME = "female"
DEFAULT_PATIENT_BIRTH_DATE = date(1970, 1, 1)


class _InformationSourceManager(Protocol):
    def resolve_by_name(self, name: str) -> InformationSource | None: ...


PatientKwargValue: TypeAlias = str | bool | date | Gender | Center | None


def get_information_source_prediction() -> InformationSource:
    from .data_loader import load_information_source_data

    load_information_source_data()
    source = cast(
        _InformationSourceManager,
        InformationSource.objects,
    ).resolve_by_name("prediction")
    assert isinstance(source, InformationSource), (
        "No InformationSource found in the database."
    )
    return source


def get_latest_segmentation_model(
    model_name: str = DEFAULT_SEGMENTATION_MODEL_NAME,
) -> ModelMeta:
    """
    Get the latest segmentation model from the database.
    This function retrieves the latest ModelMeta object from the database.
    Returns:
        ModelMeta: The latest segmentation model.
    """
    from .data_loader import (
        load_ai_model_data,
        load_ai_model_label_data,
        load_center_data,
        load_default_ai_model,
    )

    load_center_data()
    load_ai_model_label_data()
    load_ai_model_data()

    ai_model = AiModel.objects.filter(name=model_name).first()
    if ai_model is not None:
        try:
            latest_meta = ai_model.get_latest_version()
            ensure_managed_stub_weights(
                latest_meta,
                suffix=f"{model_name}_stub.safetensors",
            )
            return latest_meta
        except ValueError:
            pass

    load_default_ai_model()
    ai_model = AiModel.objects.get(name=model_name)
    latest_meta = ai_model.get_latest_version()
    ensure_managed_stub_weights(
        latest_meta,
        suffix=f"{model_name}_stub.safetensors",
    )
    return latest_meta


def get_default_gender() -> Gender:
    return Gender.objects.get(name=DEFAULT_GENDER)


def get_gender_m_or_f() -> Gender:
    return Gender.objects.get(name=DEFAULT_GENDER)


def get_random_gender() -> Gender:
    """
    Returns a randomly selected Gender object from the predefined list of default genders.
    """
    gender_name = random.choice(DEFAULT_GENDERS)
    return Gender.objects.get(name=gender_name)


def generate_gender(name: str | None = None) -> Gender:
    """
    Retrieves a Gender object by name, defaulting to "unknown" if no name is provided.

    Args:
        name: The name of the gender to retrieve. If None, uses the default gender.

    Returns:
        The Gender object matching the specified name.

    Raises:
        ValueError: If no Gender object with the given name exists.
    """
    if not name:
        name = DEFAULT_GENDER

    gender = Gender.objects.filter(name=name).first()
    if not gender:
        raise ValueError
    return gender


def get_default_processor() -> EndoscopyProcessor:
    """
    Retrieves the default EndoscopyProcessor by name.

    Returns:
        The EndoscopyProcessor instance with the default processor name.

    Raises:
        AssertionError: If no EndoscopyProcessor with the default name exists.
    """
    processor = EndoscopyProcessor.objects.get(name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME)
    assert isinstance(processor, EndoscopyProcessor), (
        "No EndoscopyProcessor found in the database."
    )
    return processor


def get_default_center() -> Center:
    """
    Create a default Center instance for testing.
    """
    center = Center.objects.get(
        name=DEFAULT_CENTER_NAME,
    )
    assert isinstance(center, Center), (
        f"Center with name {DEFAULT_CENTER_NAME} not found."
    )
    return center


def _coerce_optional_str(value: PatientKwargValue, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise TypeError(f"{field_name} must be a string or None")


def _coerce_birth_date(value: PatientKwargValue) -> date:
    if value is None:
        return DEFAULT_PATIENT_BIRTH_DATE
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("birth_date/dob must be a date, ISO date string, or None")


def _coerce_gender(value: PatientKwargValue, *, randomize: bool) -> Gender:
    if value is None:
        if randomize:
            return get_random_gender()
        return generate_gender(name=DEFAULT_PATIENT_GENDER_NAME)
    if isinstance(value, Gender):
        return value
    if isinstance(value, str):
        return generate_gender(name=value)
    raise TypeError("gender must be a Gender, string, or None")


def _coerce_center(value: PatientKwargValue) -> Center:
    if value is None:
        return get_default_center()
    if isinstance(value, Center):
        return value
    if isinstance(value, str):
        return Center.objects.get(name=value)
    raise TypeError("center must be a Center, string, or None")


def generate_patient(**kwargs: PatientKwargValue) -> Patient:
    """Create a test Patient with deterministic defaults unless randomness is requested."""

    randomize = bool(kwargs.pop("randomize", False))

    gender = _coerce_gender(kwargs.get("gender"), randomize=randomize)

    first_name = _coerce_optional_str(kwargs.get("first_name"), "first_name")
    last_name = _coerce_optional_str(kwargs.get("last_name"), "last_name")

    if first_name is None or last_name is None:
        if randomize:
            generated_first, generated_last = create_mock_patient_name(
                gender=gender.name
            )
        else:
            generated_first, generated_last = (
                DEFAULT_PATIENT_FIRST_NAME,
                DEFAULT_PATIENT_LAST_NAME,
            )
        first_name = first_name or generated_first
        last_name = last_name or generated_last

    dob_value = kwargs.get("dob")
    if dob_value is None:
        dob = _coerce_birth_date(kwargs.get("birth_date"))
    else:
        dob = _coerce_birth_date(dob_value)

    center = _coerce_center(kwargs.get("center"))

    patient = Patient(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center=center,
        gender=gender,
    )

    return patient


def get_random_default_examination() -> Examination:
    """
    Get a random examination type from the list of default examinations.
    Returns:
        Examination: A random examination object.
    """
    examination_name = random.choice(DEFAULT_EXAMINATIONS)

    examination = Examination.objects.get(name=examination_name)
    return examination


def get_random_default_examination_indication() -> ExaminationIndication:
    """
    Get a random examination indication from the list of default indications.
    Returns:
        ExaminationIndication: A random examination indication object.
    """
    examination_indication_name = random.choice(DEFAULT_INDICATIONS)
    all_examination_indications = ExaminationIndication.objects.all()
    try:
        examination_indication = ExaminationIndication.objects.get(
            name=examination_indication_name
        )

    except Exception as e:
        logger.info("examination_indication: %s", examination_indication_name)
        logger.info("all_examination_indications: %s", all_examination_indications)
        raise e
    return examination_indication


def get_default_egd_pdf():
    """
    Get a default EGD report file for testing.
    This function creates a temporary copy of the default report file, uses it to create and save
    a RawPdfFile instance using the refactored create_from_file method,
    processes it to create SensitiveMeta, and ensures that the temporary file is deleted.

    Returns:
        RawPdfFile: The created and processed RawPdfFile instance.
    """
    egd_path = DEFAULT_EGD_PATH
    center = get_default_center()
    center_name = center.name

    temp_dir = Path(settings.MEDIA_ROOT) / "temp_test_files"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file_path = temp_dir / f"temp_{egd_path.name}"

    atomic_copy_file(source=egd_path, destination=temp_file_path)

    pdf_file = None
    file_field: Optional[FieldFile] = None
    try:
        pdf_file = create_initialized_raw_pdf_file_from_path(
            file_path=temp_file_path,
            center_name=center_name,
        )

        assert pdf_file is not None, "Failed to create report file object"

        file_field = pdf_file.file
        assert isinstance(file_field, FieldFile)
        assert isinstance(file_field.name, str)
        assert default_storage.exists(file_field.name), (
            f"report file does not exist in storage at {file_field.name}"
        )

        safe_unlink_file(temp_file_path, missing_ok=True)

        default_report_meta = cast(
            "ReportMetaJsonObject",
            {
                "patient_first_name": "DefaultFirstName",
                "patient_last_name": "DefaultLastName",
                "patient_dob": date(1980, 1, 1),
                "examination_date": date(2024, 1, 1),
            },
        )

        process_raw_pdf_file(
            pdf_file,
            text="Default report text content.",
            anonymized_text="Default anonymized report text content.",
            report_meta=default_report_meta,
            verbose=False,
        )

    except Exception as e:
        if temp_file_path.exists():
            safe_unlink_file(temp_file_path)
        raise e

    try:
        logger.info(
            "report file created: %s, Path: %s",
            file_field.name,
            file_field.path,
        )
    except NotImplementedError:
        logger.info(
            "report file created: %s, Path: (Not available from storage)",
            file_field.name,
        )

    return pdf_file


def get_default_video_file():
    """
    Creates and initializes a default VideoFile instance for an EGD examination.

    Loads required datasets, selects a random EGD video, creates a VideoFile object with default center and processor, initializes its metadata and frames, and saves the updated instance.

    Returns:
        The created and initialized VideoFile instance.
    """
    from endoreg_db.models import VideoFile

    from ..media.video.helper import get_random_video_path_by_examination_alias
    from .data_loader import load_base_db_data

    load_base_db_data()
    video_path = get_random_video_path_by_examination_alias(
        examination_alias="egd", is_anonymous=False
    )

    video_file = VideoFile.create_from_file_initialized(
        file_path=video_path,
        center_name=DEFAULT_CENTER_NAME,
        processor_name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
        video_hash=sha256_file(video_path),
    )

    return video_file
