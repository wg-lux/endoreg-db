from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from endoreg_db.exceptions import (
    DicomConcurrentImportConflictError,
    DicomImportConflictError,
    DicomManifestValidationError,
    InteroperabilityErrorCode,
)
from endoreg_db.services.interoperability import (
    DicomImportConflictError as ExportedDicomImportConflictError,
)
from endoreg_db.views.interoperability_errors import interoperability_error_response


def test_interoperability_response_maps_conflict_and_retry_contract() -> None:
    ordinary = interoperability_error_response(DicomImportConflictError("internal"))
    concurrent = interoperability_error_response(
        DicomConcurrentImportConflictError("internal")
    )
    ordinary_payload = cast(Mapping[str, object], ordinary.data)
    concurrent_payload = cast(Mapping[str, object], concurrent.data)

    assert ordinary.status_code == 409
    assert ordinary_payload["retryable"] is False
    assert concurrent.status_code == 409
    assert concurrent_payload["retryable"] is True
    assert concurrent_payload["code"] == "dicom_concurrent_identity_conflict"
    assert "internal" not in str(concurrent_payload)


def test_interoperability_response_maps_validation_contract() -> None:
    response = interoperability_error_response(
        DicomManifestValidationError("sensitive source detail")
    )

    assert response.status_code == 422
    assert response.data == {
        "code": InteroperabilityErrorCode.DICOM_MANIFEST_INVALID.value,
        "detail": "The DICOM export manifest is invalid.",
        "retryable": False,
    }


def test_dicom_exception_public_import_remains_compatible() -> None:
    assert ExportedDicomImportConflictError is DicomImportConflictError
