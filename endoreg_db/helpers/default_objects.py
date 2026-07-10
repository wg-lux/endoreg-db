from __future__ import annotations

import random
from datetime import date
from logging import getLogger
from pathlib import Path
from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypedDict, Unpack, cast

from django.conf import settings  # Import settings
from django.core.files.storage import default_storage  # Import default storage
from django.db.models.fields.files import FieldFile

from endoreg_db.services import raw_pdf_files as raw_pdf_file_services
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    ensure_directory,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.examination.examination_indication import (
    ExaminationIndication,
)
from endoreg_db.models.medical.hardware.endoscopy_processor import EndoscopyProcessor
from endoreg_db.models.metadata.model_meta import ModelMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.utils import create_mock_patient_name

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
    from endoreg_db.models.media.video.video_file import VideoFile

logger = getLogger("default_objects")

type Null = NoneType
type ReportMetaValue = str | date
type ReportMeta = dict[str, ReportMetaValue]


class _InformationSourceManager(Protocol):
    def resolve_by_name(self, name: str) -> InformationSource | Null: ...


class _GenderManager(Protocol):
    def resolve_by_name(
        self,
        name: str,
        *,
        case_insensitive: bool = True,
    ) -> Gender | Null: ...


class _NamedModel(Protocol):
    name: str


class GeneratePatientKwargs(TypedDict, total=False):
    randomize: bool
    gender: Gender | str
    first_name: str
    last_name: str
    dob: date | str
    birth_date: date | str
    center: Center | str


class _CreateRawPdfFileFromPath(Protocol):
    def __call__(
        self,
        file_path: str | Path,
        center_name: str,
        *,
        save: bool = True,
    ) -> RawPdfFile: ...


class _ProcessRawPdfFile(Protocol):
    def __call__(
        self,
        report: RawPdfFile,
        text: str,
        anonymized_text: str,
        report_meta: ReportMeta,
        verbose: bool,
    ) -> tuple[str, str, ReportMeta]: ...


def _information_source_manager() -> _InformationSourceManager:
    return cast(_InformationSourceManager, InformationSource.objects)


def _gender_manager() -> _GenderManager:
    return cast(_GenderManager, Gender.objects)


def _get_gender_by_name(name: str) -> Gender:
    gender = _gender_manager().resolve_by_name(name)
    if gender is None:
        raise Gender.DoesNotExist(name)
    return gender


def _create_raw_pdf_file_from_path(
    *,
    file_path: Path,
    center_name: str,
    save: bool = True,
) -> RawPdfFile:
    create_raw_pdf_file = cast(
        _CreateRawPdfFileFromPath,
        raw_pdf_file_services.create_raw_pdf_file_from_path,
    )
    return create_raw_pdf_file(
        file_path=file_path,
        center_name=center_name,
        save=save,
    )


def _process_raw_pdf_file(
    report: RawPdfFile,
    *,
    text: str,
    anonymized_text: str,
    report_meta: ReportMeta,
    verbose: bool,
) -> tuple[str, str, ReportMeta]:
    process_raw_pdf_file = cast(
        _ProcessRawPdfFile,
        raw_pdf_file_services.process_raw_pdf_file,
    )
    return process_raw_pdf_file(
        report,
        text=text,
        anonymized_text=anonymized_text,
        report_meta=report_meta,
        verbose=verbose,
    )


DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
DEFAULT_ENDOSCOPE_NAME = "test_endoscope"
DEFAULT_ENDOSCOPY_PROCESSOR_NAME = "olympus_cv_1500"

DEFAULT_EGD_PATH = Path("tests/assets/lux-gastro-report.pdf")
DEFAULT_GENDERS: tuple[str, ...] = ("male", "female", "unknown")
DEFAULT_EXAMINATIONS: tuple[str, ...] = ("colonoscopy",)
DEFAULT_INDICATIONS: tuple[str, ...] = (
    "colonoscopy",
    "colonoscopy_screening",
    "colonoscopy_lesion_removal_small",
    "colonoscopy_lesion_removal_emr",
    "colonoscopy_lesion_removal_large",
    "colonoscopy_diagnostic_acute_symptomatic",
)

DEFAULT_SEGMENTATION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"

DEFAULT_GENDER = "unknown"
DEFAULT_PATIENT_FIRST_NAME = "TestFirst"
DEFAULT_PATIENT_LAST_NAME = "TestLast"
DEFAULT_PATIENT_GENDER_NAME = "female"
DEFAULT_PATIENT_BIRTH_DATE = date(1970, 1, 1)


def get_information_source_prediction() -> InformationSource:
    """
    Retrieves the InformationSource instance with the name "prediction".

    Loads information source data if needed and returns the corresponding InformationSource instance. Raises a ValueError if the source is not found.
    """
    from .data_load_orchestrator import load_information_source

    load_information_source()
    source = _information_source_manager().resolve_by_name("prediction")
    if source is None:
        raise ValueError("No InformationSource found in the database.")
    return source


def get_latest_segmentation_model(
    model_name: str = DEFAULT_SEGMENTATION_MODEL_NAME,
) -> ModelMeta:
    """
    Retrieves the latest metadata for a segmentation model by name.

    Loads necessary data and returns the most recent ModelMeta instance for the specified AI model. If no metadata exists, attempts to initialize it automatically; if initialization fails, raises a ValueError with instructions for manual setup.

    Args:
        model_name: The name of the segmentation model to retrieve.

    Returns:
        The latest ModelMeta instance for the specified model.

    Raises:
        ValueError: If the AI model does not exist, or if model metadata cannot be found or initialized.
    """
    from .data_load_orchestrator import (
        load_ai_model_data,
        load_ai_model_label_data,
        load_center_data,
    )

    load_center_data()
    load_ai_model_label_data()
    load_ai_model_data()

    try:
        ai_model = AiModel.objects.get(name=model_name)
    except AiModel.DoesNotExist:
        raise ValueError(
            f"AI model '{model_name}' not found. Run 'python manage.py load_ai_model_data' first."
        )

    try:
        latest_meta = ai_model.get_latest_version()
        return latest_meta
    except ValueError as e:
        if "No model metadata found" in str(e):
            logger.warning(
                f"No ModelMeta found for {model_name}. Attempting to initialize default model metadata..."
            )

            # Try to initialize the default model metadata
            try:
                from django.core.management import call_command

                call_command("init_default_ai_model")
                # Try again after initialization
                latest_meta = ai_model.get_latest_version()
                return latest_meta
            except Exception as init_error:
                raise ValueError(
                    f"No model metadata found for AI model '{model_name}' and failed to auto-initialize. "
                    f"Please run 'python manage.py init_default_ai_model' manually. "
                    f"Original error: {e}. Initialization error: {init_error}"
                ) from e
        else:
            raise


def get_default_gender() -> Gender:
    """
    Retrieves the Gender instance representing the default "unknown" gender.

    Returns:
        The Gender instance with the name "unknown".
    """
    return _get_gender_by_name(DEFAULT_GENDER)


def get_gender_m_or_f() -> Gender:
    """
    Returns a randomly selected Gender instance representing either male or female.
    """
    gender_name = random.choice(["male", "female"])
    return _get_gender_by_name(gender_name)


def get_random_gender() -> Gender:
    """
    Returns a randomly selected Gender instance from the available default genders.
    """
    gender_name = random.choice(DEFAULT_GENDERS)
    return _get_gender_by_name(gender_name)


def get_default_processor() -> EndoscopyProcessor:
    """
    Retrieves the default EndoscopyProcessor instance by its predefined name.

    Raises:
        ValueError: If no EndoscopyProcessor with the default name exists.

    Returns:
        The EndoscopyProcessor instance with the default name.
    """
    processor = EndoscopyProcessor.objects.get(name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME)
    return processor


def get_default_center() -> Center:
    """
    Retrieves the default Center instance with the predefined name.

    Raises:
        ValueError: If no Center with the default name exists.

    Returns:
        The Center instance with the default name.
    """
    center = Center.objects.get(
        name=DEFAULT_CENTER_NAME,
    )
    return center


def _resolve_patient_gender(
    gender_input: Gender | str | Null,
    *,
    randomize: bool,
) -> Gender:
    if isinstance(gender_input, Gender):
        return gender_input
    if gender_input is not None:
        return _get_gender_by_name(gender_input)
    if randomize:
        return get_random_gender()
    return _get_gender_by_name(DEFAULT_PATIENT_GENDER_NAME)


def _resolve_patient_center(center_input: Center | str | Null) -> Center:
    if isinstance(center_input, Center):
        return center_input
    if center_input is None:
        return get_default_center()
    return Center.objects.get(name=center_input)


def _resolve_patient_date(date_input: date | str) -> date:
    if isinstance(date_input, date):
        return date_input
    return date.fromisoformat(date_input)


def generate_patient(**kwargs: Unpack[GeneratePatientKwargs]) -> Patient:
    """Create a Patient with deterministic defaults unless ``randomize=True`` is supplied."""

    randomize = kwargs.get("randomize", False)
    gender = _resolve_patient_gender(kwargs.get("gender"), randomize=randomize)

    first_name = kwargs.get("first_name")
    last_name = kwargs.get("last_name")
    if first_name is None or last_name is None:
        if randomize:
            named_gender = cast(_NamedModel, gender)
            generated_first, generated_last = create_mock_patient_name(
                gender=named_gender.name
            )
        else:
            generated_first, generated_last = (
                DEFAULT_PATIENT_FIRST_NAME,
                DEFAULT_PATIENT_LAST_NAME,
            )
        first_name = first_name or generated_first
        last_name = last_name or generated_last

    dob_input = kwargs.get("dob")
    if dob_input is None:
        birth_date_input = kwargs.get("birth_date", DEFAULT_PATIENT_BIRTH_DATE)
        dob = _resolve_patient_date(birth_date_input)
    else:
        dob = _resolve_patient_date(dob_input)

    center = _resolve_patient_center(kwargs.get("center"))

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
    Retrieves a random Examination instance from the default examination names.

    Returns:
        Examination: A randomly selected Examination instance from the defaults.
    """
    examination_name = random.choice(DEFAULT_EXAMINATIONS)

    examination = Examination.objects.get(name=examination_name)
    return examination


def get_random_default_examination_indication() -> ExaminationIndication:
    """
    Returns a random ExaminationIndication instance from the default indications list.

    Selects a random indication name from the predefined defaults and retrieves the corresponding ExaminationIndication instance from the database.
    """
    examination_indication = random.choice(DEFAULT_INDICATIONS)
    all_examination_indications = ExaminationIndication.objects.all()
    try:
        examination_indication = ExaminationIndication.objects.get(
            name=examination_indication
        )

    except Exception as e:
        logger.info(f"examination_indication: {examination_indication}")
        logger.info(f"all_examination_indications: {all_examination_indications}")
        raise e
    return examination_indication


def get_default_egd_pdf() -> RawPdfFile:
    """
    Creates and processes a default EGD report file for testing purposes.

    This function copies a default EGD report to a temporary location, creates a RawPdfFile instance from it, processes the file to generate associated metadata, and ensures cleanup of the temporary file. The resulting RawPdfFile instance is returned for use in tests.

    Returns:
        RawPdfFile: The created and processed RawPdfFile instance.
    """
    egd_path = DEFAULT_EGD_PATH
    center = get_default_center()
    named_center = cast(_NamedModel, center)
    center_name = named_center.name

    # Create a temporary file path within the test's media root if possible,
    # otherwise use the source directory. Using MEDIA_ROOT is safer.
    # Ensure MEDIA_ROOT is configured correctly in test settings.
    temp_dir = ensure_directory(Path(settings.MEDIA_ROOT) / "temp_test_files")
    temp_file_path = temp_dir / f"temp_{egd_path.name}"

    atomic_copy_file(source=egd_path, destination=temp_file_path)

    try:
        # Create the report record using the temporary file.
        pdf_file = _create_raw_pdf_file_from_path(
            file_path=temp_file_path,
            center_name=center_name,
            save=True,  # save=True is default and handled internally now
        )

        # Use storage API to check existence
        file_field: FieldFile = pdf_file.file
        file_name = file_field.name
        if not file_name:
            raise RuntimeError("RawPdfFile.file did not have a storage name")
        if not default_storage.exists(file_name):
            raise RuntimeError(f"report file does not exist in storage at {file_name}")

        # Check that the source temp file was deleted
        if temp_file_path.exists():
            raise RuntimeError(
                f"Temporary source file {temp_file_path} still exists after creation"
            )

        # Prepare a minimal report_meta for SensitiveMeta creation
        default_report_meta: ReportMeta = {
            "patient_first_name": "DefaultFirstName",
            "patient_last_name": "DefaultLastName",
            "patient_dob": date(1980, 1, 1),
            "examination_date": date(2024, 1, 1),
            # center_name will be added by process_file using pdf_file.center.name
        }

        # Call service to create SensitiveMeta and extract other info
        _process_raw_pdf_file(
            pdf_file,
            text="Default report text content.",
            anonymized_text="Default anonymized report text content.",
            report_meta=default_report_meta,
            verbose=False,
        )
        # process_file calls sensitive_meta.save() and self.save() (for RawPdfFile)

    except Exception as e:
        # Clean up temp file in case of error before deletion could occur
        if temp_file_path.exists():
            safe_unlink_file(temp_file_path)
        raise e  # Re-raise the exception

    logger.info("report file created: %s", file_field.name)

    return pdf_file


def get_default_video_file() -> VideoFile:
    """
    Creates and returns a VideoFile instance using a randomly selected EGD examination video.

    Loads all necessary data dependencies, selects a random video file for the 'egd' examination, and initializes a VideoFile instance with the default center and processor names. The original video file is retained after creation.

    Returns:
        VideoFile: The created and initialized VideoFile instance.
    """
    from endoreg_db.services.video_files import create_initialized_video_file_from_path

    from .data_load_orchestrator import (
        load_ai_model_data,
        load_ai_model_label_data,
        load_center_data,
        load_disease_data,
        load_endoscope_data,
        load_event_data,
        load_examination_data,
        load_information_source,
    )
    from tests.helpers.test_video_helper import (
        get_random_video_path_by_examination_alias,
    )

    load_disease_data()
    load_event_data()
    load_information_source()
    load_examination_data()
    load_center_data()
    load_endoscope_data()
    load_ai_model_label_data()
    load_ai_model_data()
    video_path = get_random_video_path_by_examination_alias(
        examination_alias="egd", is_anonymous=False
    )

    video_file = create_initialized_video_file_from_path(
        file_path=video_path,
        center_name=DEFAULT_CENTER_NAME,  # Pass center name as expected by _create_from_file
        processor_name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
        video_hash=sha256_file(video_path),
    )

    return video_file
