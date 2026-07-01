from __future__ import annotations

import pytest

from endoreg_db.models.state.anonymization import (
    AnonymizationState,
    derive_report_anonymization_state,
    derive_video_anonymization_state,
)
from endoreg_db.utils.rust_backend import (
    derive_anonymization_status,
    derive_report_anonymization_status,
)


@pytest.mark.parametrize(
    ("flags", "expected_status"),
    [
        (
            {"processing_error": True, "anonymization_validated": True},
            AnonymizationState.FAILED,
        ),
        ({"anonymization_validated": True}, AnonymizationState.VALIDATED),
        (
            {"sensitive_meta_processed": True},
            AnonymizationState.DONE_PROCESSING_ANONYMIZATION,
        ),
        (
            {"frames_extracted": True, "anonymized": False},
            AnonymizationState.PROCESSING_ANONYMIZING,
        ),
        (
            {"was_created": True, "frames_extracted": False},
            AnonymizationState.EXTRACTING_FRAMES,
        ),
        (
            {"was_created": False, "processing_started": True},
            AnonymizationState.STARTED,
        ),
        ({"was_created": False, "anonymized": True}, AnonymizationState.ANONYMIZED),
        ({"was_created": False}, AnonymizationState.NOT_STARTED),
    ],
)
def test_derive_anonymization_status_matches_python_status_tokens(
    flags: dict[str, bool],
    expected_status: AnonymizationState,
) -> None:
    defaults = {
        "processing_error": False,
        "anonymization_validated": False,
        "sensitive_meta_processed": False,
        "frames_extracted": False,
        "anonymized": False,
        "was_created": True,
        "processing_started": False,
    }
    defaults.update(flags)

    status = derive_anonymization_status(**defaults)

    if status is None:
        pytest.skip("Rust backend is not available in this environment.")
    assert status == expected_status.value
    assert derive_video_anonymization_state(**defaults) == expected_status


@pytest.mark.parametrize(
    ("flags", "expected_status"),
    [
        (
            {"anonymization_validated": True, "processing_error": True},
            AnonymizationState.VALIDATED,
        ),
        (
            {"sensitive_meta_processed": True, "processing_error": True},
            AnonymizationState.DONE_PROCESSING_ANONYMIZATION,
        ),
        (
            {"processing_started": True, "anonymized": False},
            AnonymizationState.PROCESSING_ANONYMIZING,
        ),
        (
            {
                "processing_started": True,
                "processing_error": True,
                "anonymized": False,
            },
            AnonymizationState.FAILED,
        ),
        (
            {"processing_started": True, "anonymized": True},
            AnonymizationState.STARTED,
        ),
        ({"anonymized": True}, AnonymizationState.ANONYMIZED),
        ({}, AnonymizationState.NOT_STARTED),
    ],
)
def test_derive_report_anonymization_status_matches_python_status_tokens(
    flags: dict[str, bool],
    expected_status: AnonymizationState,
) -> None:
    defaults = {
        "processing_error": False,
        "anonymization_validated": False,
        "sensitive_meta_processed": False,
        "anonymized": False,
        "processing_started": False,
    }
    defaults.update(flags)

    status = derive_report_anonymization_status(**defaults)

    if status is None:
        pytest.skip("Rust backend is not available in this environment.")
    assert status == expected_status.value
    assert derive_report_anonymization_state(**defaults) == expected_status
