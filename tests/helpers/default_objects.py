import random
from endoreg_db.models import (
    Center, 
    Gender, 
    Patient,
    Examination,
    ExaminationIndication,
    RawPdfFile,
    EndoscopyProcessor,
    ModelMeta,
    InformationSource,
    AiModel,
)
from logging import getLogger
from datetime import date
import shutil
from pathlib import Path
from django.conf import settings # Import settings
from django.core.files.storage import default_storage # Import default storage

from endoreg_db.utils import (
    create_mock_patient_name,
)

logger = getLogger("default_objects")


DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
DEFAULT_ENDOSCOPE_NAME = "test_endoscope"
DEFAULT_ENDOSCOPY_PROCESSOR_NAME = "olympus_cv_1500"

DEFAULT_EGD_PATH = Path("tests/assets/lux-gastro-report.pdf")
DEFAULT_GENDERS = ["male","female","unknown"]
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

def get_information_source_prediction():
    from .data_loader import load_information_source_data
    load_information_source_data()
    source = InformationSource.objects.get(name="prediction")
    assert isinstance(source, InformationSource), "No InformationSource found in the database."
    return source

def get_latest_segmentation_model(model_name:str=DEFAULT_SEGMENTATION_MODEL_NAME) -> ModelMeta:
    """
    Get the latest segmentation model from the database.
    This function retrieves the latest ModelMeta object from the database.
    Returns:
        ModelMeta: The latest segmentation model.
    """
    from .data_loader import (
        load_center_data,
        load_ai_model_label_data,
        load_ai_model_data,
    )
    load_center_data()
    load_ai_model_label_data()
    load_ai_model_data()
    ai_model = AiModel.objects.get(name=model_name)
    latest_meta = ai_model.get_latest_version()
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
    return Gender.objects.get(name=gender_name) # Fetch and return the Gender object

def generate_gender(name: str|None = None):
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
    assert isinstance(processor, EndoscopyProcessor), "No EndoscopyProcessor found in the database."
    return processor

def get_default_center() -> Center:
    """
    Create a default Center instance for testing.
    """
    center = Center.objects.get(
        name=DEFAULT_CENTER_NAME,
    )
    assert isinstance(center, Center), f"Center with name {DEFAULT_CENTER_NAME} not found."
    return center

def generate_patient(**kwargs) -> Patient:
    """
    Generate a patient with random attributes.
    This function creates a Patient instance with random attributes such as first name, last name, date of birth, and center.
    The attributes are generated using the Faker library and can be overridden by providing keyword arguments.

    Parameters:
        **kwargs: Optional keyword arguments to override default values.

    
    """
    # Set default values
    gender = kwargs.get("gender", get_random_gender())
    if not isinstance(gender, Gender):
        assert isinstance(gender, str)
        gender = Gender.objects.get(name=gender)
    first_name, last_name = create_mock_patient_name(gender = gender.name)
    first_name = kwargs.get("first_name", first_name)
    last_name = kwargs.get("last_name", last_name)
    birth_date = kwargs.get("birth_date", "1970-01-01")
    dob = date.fromisoformat(birth_date)
    center = kwargs.get("center", None)
    if center is None:
        center = get_default_center()
    else:
        center = Center.objects.get(name=center)

    patient = Patient(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center = center,
        gender = gender,
    )

    return patient
    
def get_random_default_examination():
    """
    Get a random examination type from the list of default examinations.
    Returns:
        str: A random examination type.
    """
    examination_name = random.choice(DEFAULT_EXAMINATIONS)

    examination = Examination.objects.get(name=examination_name)
    return examination

def get_random_default_examination_indication():
    """
    Get a random examination indication from the list of default indications.
    Returns:
        str: A random examination indication.
    """
    examination_indication = random.choice(DEFAULT_INDICATIONS)
    all_examination_indications = ExaminationIndication.objects.all()
    try:
        examination_indication = ExaminationIndication.objects.get(name=examination_indication)
        
    except Exception as e:
        logger.info(f"examination_indication: {examination_indication}")
        logger.info(f"all_examination_indications: {all_examination_indications}")
        raise e
    return examination_indication

def get_default_egd_pdf():
    """
    Get a default EGD PDF file for testing.
    This function creates a temporary copy of the default PDF file, uses it to create and save
    a RawPdfFile instance using the refactored create_from_file method,
    processes it to create SensitiveMeta, and ensures that the temporary file is deleted.

    Returns:
        RawPdfFile: The created and processed RawPdfFile instance.
    """
    egd_path = DEFAULT_EGD_PATH
    center = get_default_center()
    center_name = center.name

    # Create a temporary file path within the test's media root if possible,
    # otherwise use the source directory. Using MEDIA_ROOT is safer.
    # Ensure MEDIA_ROOT is configured correctly in test settings.
    temp_dir = Path(settings.MEDIA_ROOT) / "temp_test_files"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file_path = temp_dir / f"temp_{egd_path.name}"

    shutil.copy(egd_path, temp_file_path)

    pdf_file = None
    try:
        # Create the PDF record using the temporary file.
        # delete_source=True will ensure temp_file_path is deleted by create_from_file
        pdf_file = RawPdfFile.create_from_file(
            file_path=temp_file_path,
            center_name=center_name,
            save=True, # save=True is default and handled internally now
            delete_source=True,
        )

        assert pdf_file is not None, "Failed to create PDF file object"
        # Use storage API to check existence
        assert default_storage.exists(pdf_file.file.path), f"PDF file does not exist in storage at {pdf_file.file.path}"
        # Check that the source temp file was deleted
        assert not temp_file_path.exists(), f"Temporary source file {temp_file_path} still exists after creation"

        # Prepare a minimal report_meta for SensitiveMeta creation
        default_report_meta = {
            "patient_first_name": "DefaultFirstName",
            "patient_last_name": "DefaultLastName",
            "patient_dob": date(1980, 1, 1), # Pass date object directly
            "examination_date": date(2024, 1, 1), # Pass date object directly
            # center_name will be added by process_file using pdf_file.center.name
        }

        # Call process_file to create SensitiveMeta and extract other info
        pdf_file.process_file(
            text="Default PDF text content.",
            anonymized_text="Default anonymized PDF text content.",
            report_meta=default_report_meta,
            verbose=False
        )
        # process_file calls sensitive_meta.save() and self.save() (for RawPdfFile)

    except Exception as e:
        # Clean up temp file in case of error before deletion could occur
        if temp_file_path.exists():
            temp_file_path.unlink()
        raise e # Re-raise the exception

    # pdf_file.file.path might fail if storage doesn't support direct paths (like S3)
    # Prefer using storage API for checks. Logging path if available.
    try:
        logger.info(f"PDF file created: {pdf_file.file.name}, Path: {pdf_file.file.path}")
    except NotImplementedError:
        logger.info(f"PDF file created: {pdf_file.file.name}, Path: (Not available from storage)")


    return pdf_file

def get_default_video_file():
    """
    Creates and initializes a default VideoFile instance for an EGD examination.
    
    Loads required datasets, selects a random EGD video, creates a VideoFile object with default center and processor, initializes its metadata and frames, and saves the updated instance.
    
    Returns:
        The created and initialized VideoFile instance.
    """
    from ..media.video.helper import get_random_video_path_by_examination_alias
    from endoreg_db.models import VideoFile
    from .data_loader import (
        load_disease_data,
        load_event_data,
        load_information_source_data,
        load_examination_data,
        load_center_data,
        load_endoscope_data,
        load_ai_model_label_data,
        load_ai_model_data,
    )
    
    load_disease_data()
    load_event_data()
    load_information_source_data()
    load_examination_data()
    load_center_data()
    load_endoscope_data()
    load_ai_model_label_data()
    load_ai_model_data()
    video_path = get_random_video_path_by_examination_alias(
        examination_alias='egd', is_anonymous=False
    )

    video_file = VideoFile.create_from_file_initialized(
        file_path=video_path,
        center_name=DEFAULT_CENTER_NAME,  # Pass center name as expected by _create_from_file
        delete_source=False,  # Keep the original asset for other tests
        processor_name = DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
    )

  
    return video_file
