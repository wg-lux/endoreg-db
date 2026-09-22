from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from endoreg_db.exceptions import (
    InteroperabilityError,
    InteroperabilityErrorCode,
    describe_interoperability_error,
)


_HTTP_STATUS_BY_CODE: dict[InteroperabilityErrorCode, int] = {
    InteroperabilityErrorCode.DICOM_MANIFEST_INVALID: status.HTTP_422_UNPROCESSABLE_ENTITY,
    InteroperabilityErrorCode.DICOM_ARTIFACT_INTEGRITY_FAILED: status.HTTP_422_UNPROCESSABLE_ENTITY,
    InteroperabilityErrorCode.DICOM_IDENTITY_CONFLICT: status.HTTP_409_CONFLICT,
    InteroperabilityErrorCode.DICOM_CONCURRENT_IDENTITY_CONFLICT: status.HTTP_409_CONFLICT,
    InteroperabilityErrorCode.FHIR_EXPORT_INVALID: status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def interoperability_error_response(error: InteroperabilityError) -> Response:
    """Translate a known interoperability failure into a data-minimized response."""

    descriptor = describe_interoperability_error(error)
    return Response(
        {
            "code": descriptor.code.value,
            "detail": descriptor.safe_message,
            "retryable": descriptor.retryable,
        },
        status=_HTTP_STATUS_BY_CODE[descriptor.code],
    )


__all__ = ["interoperability_error_response"]
