import hashlib
import hmac
import json
from datetime import date, datetime
from pathlib import Path
import logging
from endoreg_db.utils.rust_backend import stable_file_identity
from django.db.models.fields.files import FieldFile
from django.conf import settings
from endoreg_db.config.identity_hashing import (
    validate_identity_salt,
    current_identity_keyring,
)

logger = logging.getLogger(__name__)


def get_identity_salt() -> str:
    ring = current_identity_keyring()
    if ring is not None:
        return validate_identity_salt(ring.active.decode("utf-8"))
    return validate_identity_salt(getattr(settings, "DJANGO_SALT", None))


def get_identity_salt_fingerprint() -> str:
    return hmac.new(
        get_identity_salt().encode("utf-8"), b"endoreg-identity-salt-v1", hashlib.sha256
    ).hexdigest()


def get_patient_identity_fingerprint(
    first_name: str, last_name: str, dob: date, center_id: int
) -> str:
    """Disambiguate legacy hashes without changing their persisted identity keys."""
    birthday = dob.date() if isinstance(dob, datetime) else dob
    payload = json.dumps(
        ["patient-identity-v1", first_name, last_name, birthday.isoformat(), center_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        get_identity_salt().encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()


def get_file_hash(file: str | Path | FieldFile | None) -> str:
    """Hash local sources or stream authenticated plaintext from stored files."""
    if file is None:
        raise ValueError("HASH COULD NOT BE CREATED")
    if getattr(file, "storage", None) is not None:
        from endoreg_db.utils.storage_streaming import (
            field_file_size,
            iter_field_file_bytes,
        )

        digest = hashlib.sha256()
        size = field_file_size(file)
        if size < 0:
            raise ValueError("Stored file plaintext size must not be negative")
        consumed = 0
        if size > 0:
            for chunk in iter_field_file_bytes(
                file, start=0, end=size - 1, chunk_size=1024 * 1024
            ):
                consumed += len(chunk)
                if consumed > size:
                    raise ValueError(
                        "Stored file stream exceeds declared plaintext size"
                    )
                digest.update(chunk)
        if consumed != size or field_file_size(file) != size:
            raise ValueError(
                "Stored file plaintext size changed or stream is incomplete"
            )
        return digest.hexdigest()

    path = file if isinstance(file, (str, Path)) else getattr(file, "path", None)
    if not isinstance(path, (str, Path)):
        raise TypeError("Hash source must be a local path or stored file")
    hash_tuple = stable_file_identity(Path(path))
    if hash_tuple is not None:
        size = hash_tuple[0]
        hash_canonical = hash_tuple[2]
        logger.debug("Hash created for %s bytes", size)
        return hash_canonical
    else:
        raise ValueError("HASH COULD NOT BE CREATED")


def _get_date_hash_string(date_obj: date) -> str:
    # if date is a datetime value, convert to date
    if isinstance(date_obj, datetime):
        # warnings.warn("Date is a datetime value. Converting to date value.")
        date_obj = date_obj.date()
    elif isinstance(date_obj, str):
        # warnings.warn(f"Date is a string ({date_obj}). Converting to date value.")
        date_obj = datetime.strptime(date_obj, "%Y-%m-%d").date()

    assert isinstance(date_obj, date), "Date must be a date value"
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
    salt: str | None = None,
) -> str:
    """
    Get the string to be hashed for a patient's first name, last name, date of birth, examination date, and endoscope serial number.
    """
    salt = get_identity_salt() if salt is None else validate_identity_salt(salt)

    examination_date_str = _get_date_hash_string(examination_date)
    dob_str = _get_date_hash_string(dob)

    # Concatenate the patient's first name, last name, date of birth, examination date, endoscope serial number, and salt:
    hash_str = f"{first_name}{last_name}{dob_str}{center_name}{dob_str}{examination_date_str}{endoscope_sn}{salt}"
    return hash_str


def get_patient_hash(
    first_name: str, last_name: str, dob: date, center: str, salt: str | None = None
) -> str:
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
    # Create a hash instance using SHA-256 algorithm
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
    salt: str | None = None,
) -> str:
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
    # Create a hash instance using SHA-256 algorithm
    hash_object = hashlib.sha256(hash_str.encode())
    # Get the hexadecimal representation of the hash
    patient_examination_hash = hash_object.hexdigest()

    return patient_examination_hash


def get_examiner_hash(
    first_name: str,
    last_name: str,
    center_name: str,
    salt: str | None = None,
) -> str:
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
    # Create a hash instance using SHA-256 algorithm
    hash_object = hashlib.sha256(hash_str.encode())
    # Get the hexadecimal representation of the hash
    examiner_hash = hash_object.hexdigest()

    return examiner_hash
