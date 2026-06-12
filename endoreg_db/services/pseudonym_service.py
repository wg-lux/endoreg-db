"""
Service for generating patient pseudonyms using SensitiveMeta sensitive_meta_logic.
"""

import logging
from typing import Tuple
from django.db import transaction

from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.metadata import sensitive_meta_logic

logger = logging.getLogger(__name__)


def generate_patient_pseudonym(patient: Patient) -> Tuple[str, bool]:
    """
    Generate a pseudonym hash for an existing Patient using SensitiveMeta sensitive_meta_logic.

    Args:
        patient: The Patient instance to generate a pseudonym for

    Returns:
        Tuple of (patient_hash, persisted_flag)

    Raises:
        ValueError: If required fields are missing for hash calculation
    """
    # Validate required fields for hash calculation
    if not patient.dob:
        raise ValueError(
            "Patient date of birth (dob) is required for pseudonym generation"
        )

    if not patient.center:
        raise ValueError("Patient center is required for pseudonym generation")

    # Use existing patient_hash if it exists and is valid
    if patient.patient_hash and len(patient.patient_hash.strip()) > 0:
        logger.info(f"Patient {patient.pk} already has a hash: {patient.patient_hash}")
        return patient.patient_hash, True

    # Create a transient SensitiveMeta instance to calculate the hash
    # We don't save this instance, just use it for hash calculation
    sensitive_meta = SensitiveMeta(
        patient_first_name=patient.first_name or "",
        patient_last_name=patient.last_name or "",
        patient_dob=patient.dob,
        center=patient.center,
        patient_gender=patient.gender,  # Optional, but include if available
    )

    try:
        # Calculate the hash using the existing sensitive_meta_logic
        patient_hash = sensitive_meta_logic.calculate_patient_hash(
            sensitive_meta, salt=sensitive_meta_logic.SECRET_SALT
        )

        # Persist the hash to the Patient model
        with transaction.atomic():
            patient.patient_hash = patient_hash
            patient.save(update_fields=["patient_hash"])

        logger.info(f"Generated and persisted pseudonym for patient {patient.pk}")

        return patient_hash, True

    except ValueError:
        # Known / expected error → client error (400)
        logger.exception(
            f"Validation error while generating pseudonym for patient {patient.pk}"
        )
        raise

    except Exception:
        # Unexpected error → server error (500)
        logger.exception(
            f"Unexpected error while generating pseudonym for patient {patient.pk}"
        )
        raise


def validate_patient_for_pseudonym(patient: Patient) -> list[str]:
    """
    Validate that a patient has all required fields for pseudonym generation.

    Args:
        patient: The Patient instance to validate

    Returns:
        List of missing required fields (empty if all fields present)
    """
    missing_fields: list[str] = []

    if not patient.dob:
        missing_fields.append("dob")

    if not patient.center:
        missing_fields.append("center")

    # Note: first_name and last_name can be empty strings according to the sensitive_meta_logic,
    # so we don't require them to be non-empty

    return missing_fields
