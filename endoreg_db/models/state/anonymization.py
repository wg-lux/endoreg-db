from enum import Enum


class AnonymizationState(str, Enum):
    NOT_STARTED = "not_started"
    EXTRACTING_FRAMES = "extracting_frames"
    PROCESSING_ANONYMIZING = "processing_anonymization"
    DONE_PROCESSING_ANONYMIZATION = "done_processing_anonymization"
    VALIDATED = "validated"
    FAILED = "failed"
    STARTED = "started"
    ANONYMIZED = "anonymized"
