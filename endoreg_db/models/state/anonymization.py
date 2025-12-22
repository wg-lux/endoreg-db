from enum import Enum


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
    
