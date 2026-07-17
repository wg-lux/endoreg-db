import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class InsufficientStorageError(Exception):
    """Raised when there's not enough disk space for an operation."""

    required_space: int
    available_space: int

    def __init__(
        self,
        message: str,
        required_space: int = 0,
        available_space: int = 0,
    ) -> None:
        super().__init__(message)
        self.required_space = required_space
        self.available_space = available_space


class TranscodingError(Exception):
    """Raised when video transcoding fails."""

    pass


class VideoProcessingError(Exception):
    """Base class for video processing errors."""

    pass


class InteroperabilityErrorCode(StrEnum):
    """Stable machine-readable codes for interoperability boundaries."""

    DICOM_MANIFEST_INVALID = "dicom_manifest_invalid"
    DICOM_ARTIFACT_INTEGRITY_FAILED = "dicom_artifact_integrity_failed"
    DICOM_IDENTITY_CONFLICT = "dicom_identity_conflict"
    DICOM_CONCURRENT_IDENTITY_CONFLICT = "dicom_concurrent_identity_conflict"
    FHIR_EXPORT_INVALID = "fhir_export_invalid"


@dataclass(frozen=True, slots=True)
class InteroperabilityErrorDescriptor:
    """Safe, stable metadata shared by logging and protocol boundaries."""

    code: InteroperabilityErrorCode
    safe_message: str
    log_reason: str
    retryable: bool


class InteroperabilityError(RuntimeError):
    """Base class for expected DICOM and FHIR workflow failures."""

    descriptor: InteroperabilityErrorDescriptor


class DicomImportError(InteroperabilityError):
    """Base class for expected DICOM import failures."""


class DicomManifestValidationError(DicomImportError):
    """Raised when a DICOM manifest violates the accepted export contract."""

    descriptor = InteroperabilityErrorDescriptor(
        code=InteroperabilityErrorCode.DICOM_MANIFEST_INVALID,
        safe_message="The DICOM export manifest is invalid.",
        log_reason="invalid_manifest",
        retryable=False,
    )


class DicomArtifactIntegrityError(DicomImportError):
    """Raised when a referenced DICOM artifact fails integrity verification."""

    descriptor = InteroperabilityErrorDescriptor(
        code=InteroperabilityErrorCode.DICOM_ARTIFACT_INTEGRITY_FAILED,
        safe_message="A DICOM export artifact failed integrity verification.",
        log_reason="artifact_integrity_failed",
        retryable=False,
    )


class DicomImportConflictError(DicomImportError):
    """Raised when a valid manifest conflicts with persisted DICOM identity."""

    descriptor = InteroperabilityErrorDescriptor(
        code=InteroperabilityErrorCode.DICOM_IDENTITY_CONFLICT,
        safe_message="The DICOM export conflicts with an existing import.",
        log_reason="identity_conflict",
        retryable=False,
    )


class DicomConcurrentImportConflictError(DicomImportConflictError):
    """Raised for a retryable identity race between concurrent imports."""

    descriptor = InteroperabilityErrorDescriptor(
        code=InteroperabilityErrorCode.DICOM_CONCURRENT_IDENTITY_CONFLICT,
        safe_message="A concurrent DICOM import created an identity conflict.",
        log_reason="concurrent_identity_conflict",
        retryable=True,
    )


class FhirExportError(InteroperabilityError):
    """Base class for expected FHIR export failures."""


class FhirExportValidationError(FhirExportError):
    """Raised when persisted data cannot form the pseudonymized FHIR contract."""

    descriptor = InteroperabilityErrorDescriptor(
        code=InteroperabilityErrorCode.FHIR_EXPORT_INVALID,
        safe_message="The FHIR export cannot be created from the available data.",
        log_reason="bundle_validation_failed",
        retryable=False,
    )


def describe_interoperability_error(
    error: InteroperabilityError,
) -> InteroperabilityErrorDescriptor:
    """Return the public and operational contract for a known error."""

    return error.descriptor
