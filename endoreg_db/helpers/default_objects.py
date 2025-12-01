import random
from typing import Optional
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
from django.db.models.fields.files import FieldFile

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
DEFAULT_PATIENT_FIRST_NAME = "TestFirst"
DEFAULT_PATIENT_LAST_NAME = "TestLast"
DEFAULT_PATIENT_GENDER_NAME = "female"
DEFAULT_PATIENT_BIRTH_DATE = date(1970, 1, 1)

def get_information_source_prediction():
    """
    Retrieve the InformationSource named "prediction".
    
    Returns:
        InformationSource: The InformationSource instance with name "prediction".
    
    Raises:
        ValueError: If no InformationSource with that name exists or the retrieved object is not an InformationSource.
    """
    from .data_loader import load_information_source
    load_information_source()
    source = InformationSource.objects.get(name="prediction")
    if not isinstance(source, InformationSource):
        raise ValueError("No InformationSource found in the database.")
    return source

def get_latest_segmentation_model(model_name:str=DEFAULT_SEGMENTATION_MODEL_NAME) -> ModelMeta:

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
    from .data_loader import (
        load_center_data,
        load_ai_model_label_data,
        load_ai_model_data,
    )
    load_center_data()
    load_ai_model_label_data()
    load_ai_model_data()
    
    try:
        ai_model = AiModel.objects.get(name=model_name)
    except AiModel.DoesNotExist:
        raise ValueError(f"AI model '{model_name}' not found. Run 'python manage.py load_ai_model_data' first.")
    
    try:
        latest_meta = ai_model.get_latest_version()
        return latest_meta
    except ValueError as e:
        if "No model metadata found" in str(e):
            logger.warning(f"No ModelMeta found for {model_name}. Attempting to initialize default model metadata...")
            
            # Try to initialize the default model metadata
            try:
                from django.core.management import call_command
                call_command('init_default_ai_model')
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
    Retrieves the Gender object representing the default "unknown" gender.
    
    Returns:
        The Gender instance with the name "unknown".
    """
    return Gender.objects.get(name=DEFAULT_GENDER)

def get_gender_m_or_f() -> Gender:
    """
    Return a Gender object named either "male" or "female", chosen at random.
    
    Returns:
        Gender: The selected Gender object ("male" or "female").
    
    Raises:
        Gender.DoesNotExist: If the chosen gender name does not exist in the database.
    """
    gender_name = random.choice(["male", "female"])
    return Gender.objects.get(name=gender_name)

def get_random_gender() -> Gender:
    """
    Select a Gender chosen at random from DEFAULT_GENDERS.
    
    Returns:
        Gender: The Gender instance whose name was randomly selected.
    """
    gender_name = random.choice(DEFAULT_GENDERS)
    return Gender.objects.get(name=gender_name) # Fetch and return the Gender object

def get_default_processor() -> EndoscopyProcessor:
    """
    Retrieves the EndoscopyProcessor identified by the default processor name.
    
    Returns:
        The EndoscopyProcessor instance matching DEFAULT_ENDOSCOPY_PROCESSOR_NAME.
    
    Raises:
        ValueError: If no EndoscopyProcessor with the default name exists.
    """
    processor = EndoscopyProcessor.objects.get(name=DEFAULT_ENDOSCOPY_PROCESSOR_NAME)
    if not isinstance(processor, EndoscopyProcessor):
        raise ValueError(f"No EndoscopyProcessor found with name {DEFAULT_ENDOSCOPY_PROCESSOR_NAME}")
    return processor


def get_default_center() -> Center:
    """
    Return the Center with the configured default name.
    
    Returns:
        Center: The Center instance matching DEFAULT_CENTER_NAME.
    
    Raises:
        ValueError: If no Center exists with DEFAULT_CENTER_NAME.
    """
    center = Center.objects.get(
        name=DEFAULT_CENTER_NAME,
    )
    if not isinstance(center, Center):
        raise ValueError(f"No Center found with name {DEFAULT_CENTER_NAME}")
    
    return center

def generate_patient(**kwargs) -> Patient:
    """
    Create a Patient instance using provided values or deterministic defaults; when `randomize=True`, name and gender may be randomized.
    
    Parameters:
        randomize (bool): If True, generate randomized first and last names and choose a random gender when not provided.
        gender (str|Gender, optional): Gender name or Gender instance to assign to the patient. If omitted, defaults to the file's DEFAULT_PATIENT_GENDER_NAME or a random gender when `randomize` is True.
        first_name (str, optional): Patient's first name. If omitted, a deterministic default or a generated name (when `randomize`) is used.
        last_name (str, optional): Patient's last name. If omitted, a deterministic default or a generated name (when `randomize`) is used.
        dob (date|str, optional): Date of birth as a date or ISO-format string. If omitted, `birth_date` or DEFAULT_PATIENT_BIRTH_DATE is used.
        birth_date (date|str, optional): Alternative key for date of birth; used when `dob` is not provided.
        center (str|Center, optional): Center name or Center instance to assign. If omitted, the file's default center is used.
    
    Returns:
        Patient: An unsaved Patient instance populated with the resolved attributes.
    """

    randomize = kwargs.pop("randomize", False)

    gender = kwargs.get("gender")
    if gender is None:
        if randomize:
            gender = get_random_gender()
        else:
            gender = Gender.objects.get(name=DEFAULT_PATIENT_GENDER_NAME)
    elif not isinstance(gender, Gender):
        gender = Gender.objects.get(name=gender)

    first_name = kwargs.get("first_name")
    last_name = kwargs.get("last_name")
    if first_name is None or last_name is None:
        if randomize:
            generated_first, generated_last = create_mock_patient_name(gender=gender.name)
        else:
            generated_first, generated_last = DEFAULT_PATIENT_FIRST_NAME, DEFAULT_PATIENT_LAST_NAME
        first_name = first_name or generated_first
        last_name = last_name or generated_last

    dob = kwargs.get("dob")
    if dob is None:
        birth_date = kwargs.get("birth_date", DEFAULT_PATIENT_BIRTH_DATE)
        if isinstance(birth_date, date):
            dob = birth_date
        else:
            dob = date.fromisoformat(str(birth_date))

    center = kwargs.get("center")
    if center is None:
        center = get_default_center()
    elif not isinstance(center, Center):
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
    Selects and returns a random Examination from the default examination names.
    
    Returns:
        Examination: Examination instance chosen randomly from DEFAULT_EXAMINATIONS.
    """
    examination_name = random.choice(DEFAULT_EXAMINATIONS)

    examination = Examination.objects.get(name=examination_name)
    return examination

def get_random_default_examination_indication():
    """
    Selects a random default examination indication and returns its corresponding ExaminationIndication instance.
    
    Returns:
        The matching ExaminationIndication instance.
    
    Raises:
        Exception: Propagates any exception raised while retrieving the ExaminationIndication from the database (for example `DoesNotExist` or `MultipleObjectsReturned`).
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
    Create and process a default EGD PDF test file and return its RawPdfFile record.
    
    The function copies a predefined EGD PDF into a temporary test directory, creates a RawPdfFile from that file, runs processing to extract metadata (including creating SensitiveMeta), and ensures temporary files are cleaned up on error. The returned RawPdfFile is ready for use in tests and has been processed by process_file.
    
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
    file_field: Optional[FieldFile] = None
    try:
        # Create the PDF record using the temporary file.
        # delete_source=True will ensure temp_file_path is deleted by create_from_file
        pdf_file = RawPdfFile.create_from_file(
            file_path=temp_file_path,
            center_name=center_name,
            save=True, # save=True is default and handled internally now
            delete_source=True,
        )

        if pdf_file is None:
            raise RuntimeError("Failed to create PDF file object")
        
        # Use storage API to check existence
        file_field = pdf_file.file
        if not isinstance(file_field, FieldFile):
            raise RuntimeError("RawPdfFile.file did not return a FieldFile instance")
        if not default_storage.exists(file_field.path):
            raise RuntimeError(f"PDF file does not exist in storage at {file_field.path}")
        
        # Check that the source temp file was deleted
        if temp_file_path.exists():
            raise RuntimeError(f"Temporary source file {temp_file_path} still exists after creation")

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
    if file_field is not None:
        try:
            logger.info(f"PDF file created: {file_field.name}, Path: {file_field.path}")
        except NotImplementedError:
            logger.info(f"PDF file created: {file_field.name}, Path: (Not available from storage)")


    return pdf_file

def get_default_video_file():
    """
    Create a VideoFile from a randomly selected 'egd' examination video.
    
    This loads required test data (disease, event, information source, examinations, center, endoscope, AI model labels and data), selects a random non-anonymous EGD video, and initializes a VideoFile using the default center and processor names while preserving the original source file.
    
    Returns:
        VideoFile: The created and initialized VideoFile instance.
    """
    from .test_video_helper import get_random_video_path_by_examination_alias
    from endoreg_db.models import VideoFile
    from .data_loader import (
        load_disease_data,
        load_event_data,
        load_information_source,
        load_examination_data,
        load_center_data,
        load_endoscope_data,
        load_ai_model_label_data,
        load_ai_model_data,
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
        examination_alias='egd', is_anonymous=False
    )

    video_file = VideoFile.create_from_file_initialized(
        file_path=video_path,
        center_name=DEFAULT_CENTER_NAME,  # Pass center name as expected by _create_from_file
        delete_source=False,  # Keep the original asset for other tests
        processor_name = DEFAULT_ENDOSCOPY_PROCESSOR_NAME,
    )

    return video_file