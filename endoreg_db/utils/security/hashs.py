import hashlib
import os
from datetime import date, datetime
from pathlib import Path
import logging
from django.db.models.fields.files import FieldFile
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.storage import ensure_local_file

logger = logging.getLogger(__name__)

SALT = os.getenv("DJANGO_SALT", "default_salt")
DJANGO_NAME_SALT = os.environ.get("DJANGO_SALT", "default_salt")


def get_video_hash(video_file: Path | FieldFile) -> str:
    """Semantic alias for sha256_file() used by video import workflows."""
    return sha256_file(video_file)


def get_pdf_hash(pdf_file: Path | FieldFile) -> str:
    """Semantic alias for sha256_file() used by report import workflows."""
    return sha256_file(pdf_file)


def _sha256_field_file(field_file: FieldFile) -> str:
    """Compatibility helper for callers that need a hash from Django storage."""
    with ensure_local_file(field_file) as local_path:
        return sha256_file(local_path)


def _get_date_hash_string(date_obj: date) -> str:
    # if date is datetime object, convert to date
    if isinstance(date_obj, datetime):
        # warnings.warn("Date is a datetime object. Converting to date object.")
        date_obj = date_obj.date()
    elif isinstance(date_obj, str):
        # warnings.warn(f"Date is a string ({date_obj}). Converting to date object.")
        date_obj = datetime.strptime(date_obj, "%Y-%m-%d").date()

    assert isinstance(date_obj, date), "Date must be a date object"
    # if date is 1900-01-01, make it an empty string
    if date_obj == date(1900, 1, 1):
        date_str = ""
    else:
        date_str = date_obj.strftime("%Y-%m-%d")

    return date_str


def get_hash_string(
    first_name: str = "",
    last_name: str = "",
    dob: date = date(1900, 1, 1),
    center_name: str = "",
    examination_date: date = date(1900, 1, 1),
    endoscope_sn: str = "",
    salt: str = "",
):
    """
    Get the string to be hashed for a patient's first name, last name, date of birth, examination date, and endoscope serial number.
    """
    if not salt:
        salt = SALT

    examination_date_str = _get_date_hash_string(examination_date)
    dob_str = _get_date_hash_string(dob)

    # Concatenate the patient's first name, last name, date of birth, examination date, endoscope serial number, and salt:
    hash_str = f"{first_name}{last_name}{dob_str}{center_name}{dob_str}{examination_date_str}{endoscope_sn}{salt}"
    return hash_str


def get_patient_hash(
    first_name: str, last_name: str, dob: date, center: str, salt: str = ""
):
    """
    Get the hash of a patient's first name, last name, and date of birth.
    """
    # Concatenate the patient's first name, last name, date of birth, and salt:
    hash_str = get_hash_string(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center_name=center,
        salt=salt,
    )
    # Create a hash object using SHA-256 algorithm
    hash_object = hashlib.sha256(hash_str.encode())
    # Get the hexadecimal representation of the hash
    patient_hash = hash_object.hexdigest()

    return patient_hash


def get_patient_examination_hash(
    first_name: str,
    last_name: str,
    dob: date,
    center: str,
    examination_date: date,
    salt: str = "",
):
    """
    Get the hash of a patient's first name, last name, date of birth, and examination date.
    """
    # Concatenate the patient's first name, last name, date of birth, examination date, and salt:
    hash_str = get_hash_string(
        first_name=first_name,
        last_name=last_name,
        center_name=center,
        dob=dob,
        examination_date=examination_date,
        salt=salt,
    )
    # Create a hash object using SHA-256 algorithm
    hash_object = hashlib.sha256(hash_str.encode())
    # Get the hexadecimal representation of the hash
    patient_examination_hash = hash_object.hexdigest()

    return patient_examination_hash


def get_examiner_hash(first_name, last_name, center_name, salt):
    """
    Get the hash of an examiner's first name, last name, and center name.
    """
    # Concatenate the examiner's first name, last name, center name, and salt:
    hash_str = get_hash_string(
        first_name=first_name,
        last_name=last_name,
        center_name=center_name,
        salt=salt,
    )
    # Create a hash object using SHA-256 algorithm
    hash_object = hashlib.sha256(hash_str.encode())
    # Get the hexadecimal representation of the hash
    examiner_hash = hash_object.hexdigest()

    return examiner_hash
