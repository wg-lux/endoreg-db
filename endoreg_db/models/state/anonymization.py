from __future__ import annotations
from enum import Enum

from endoreg_db.utils.rust_backend import (
    derive_anonymization_status as rust_derive_anonymization_status,
    derive_report_anonymization_status as rust_derive_report_anonymization_status,
)


class AnonymizationState(str, Enum):
    """Enumeration for the various states of the anonymization process.

    Cheat Sheet:
    Desired Status (AnonymizationState)	Boolean Flags to Set in create()
    VALIDATED	anonymization_validated=True
    DONE_PROCESSING_ANONYMIZATION	sensitive_meta_processed=True, anonymization_validated=False
    ANONYMIZED	anonymized=True, sensitive_meta_processed=False
    PROCESSING_ANONYMIZING	processing_started=True, frames_extracted=True (Video only)
    EXTRACTING_FRAMES	was_created=True, frames_extracted=False (Video only)
    FAILED	processing_error=True (if field exists)
    STARTED	processing_started=True
    NOT_STARTED	No flags (defaults are usually False)

    Args:
        str (_type_): _description_
        Enum (_type_): _description_
    """

    NOT_STARTED = "not_started"
    EXTRACTING_FRAMES = "extracting_frames"
    PROCESSING_ANONYMIZING = "processing_anonymization"
    DONE_PROCESSING_ANONYMIZATION = "done_processing_anonymization"
    VALIDATED = "validated"
    FAILED = "failed"
    STARTED = "started"
    ANONYMIZED = "anonymized"


def derive_video_anonymization_state(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    frames_extracted: bool,
    anonymized: bool,
    was_created: bool,
    processing_started: bool,
) -> AnonymizationState:
    status = rust_derive_anonymization_status(
        processing_error=processing_error,
        anonymization_validated=anonymization_validated,
        sensitive_meta_processed=sensitive_meta_processed,
        frames_extracted=frames_extracted,
        anonymized=anonymized,
        was_created=was_created,
        processing_started=processing_started,
    )
    if status is None:
        raise RuntimeError("Rust anonymization state derivation is unavailable.")
    return AnonymizationState(status)


def derive_report_anonymization_state(
    *,
    processing_error: bool,
    anonymization_validated: bool,
    sensitive_meta_processed: bool,
    anonymized: bool,
    processing_started: bool,
) -> AnonymizationState:
    status = rust_derive_report_anonymization_status(
        processing_error=processing_error,
        anonymization_validated=anonymization_validated,
        sensitive_meta_processed=sensitive_meta_processed,
        anonymized=anonymized,
        processing_started=processing_started,
    )
    if status is None:
        raise RuntimeError("Rust report anonymization state derivation is unavailable.")
    return AnonymizationState(status)
