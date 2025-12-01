from datetime import date
from pathlib import Path

################## CENTERS & DEVICES ##################
DEFAULT_CENTER_NAME = "university_hospital_wuerzburg"
DEFAULT_ENDOSCOPE_NAME = "test_endoscope"
DEFAULT_ENDOSCOPY_PROCESSOR_NAME = "olympus_cv_1500"

################## PATIENT ##################
DEFAULT_GENDER_NAME_MALE = "male"
DEFAULT_GENDER_NAME_FEMALE = "female"
DEFAULT_GENDER_NAME_UNKNOWN = "unknown"
DEFAULT_GENDERS = [DEFAULT_GENDER_NAME_MALE, DEFAULT_GENDER_NAME_FEMALE]

DEFAULT_PATIENT_FIRST_NAME = "TestFirst"
DEFAULT_PATIENT_LAST_NAME = "TestLast"
DEFAULT_PATIENT_BIRTH_DATE = date(1970, 1, 1)

################### EXAMINATION ##################
DEFAULT_COLONOSCOPY_NAME = "colonoscopy"
DEFAULT_EXAMINATIONS_NAMES = [DEFAULT_COLONOSCOPY_NAME]

################### AI MODEL ##################
DEFAULT_SEGMENTATION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"

################### PATHS ##################
DEFAULT_EGD_REPORT_PDF_PATH = Path("tests/assets/lux-gastro-report.pdf")
DEFAULT_EGD_HISTO_PDF_PATH = Path("tests/assets/lux-gastro-histo-report.pdf")

__all__ = [
    "DEFAULT_CENTER_NAME",
    "DEFAULT_ENDOSCOPE_NAME",
    "DEFAULT_ENDOSCOPY_PROCESSOR_NAME",
    "DEFAULT_GENDER_NAME_FEMALE",
    "DEFAULT_GENDER_NAME_MALE",
    "DEFAULT_GENDERS",
    "DEFAULT_PATIENT_FIRST_NAME",
    "DEFAULT_PATIENT_LAST_NAME",
    "DEFAULT_PATIENT_BIRTH_DATE",
    "DEFAULT_EXAMINATIONS_NAMES",
    "DEFAULT_SEGMENTATION_MODEL_NAME",
    "DEFAULT_EGD_REPORT_PDF_PATH",
    "DEFAULT_EGD_HISTO_PDF_PATH",
]
