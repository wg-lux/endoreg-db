from __future__ import annotations

import pytest


@pytest.mark.django_db
@pytest.mark.xfail(
    reason="Needs minimal clinical fixtures + migration in test db for PatientExaminationReport"
)
def test_save_report_submission_is_advisory_for_unmet_requirements_scaffold():
    """
    Scaffold for report persistence service.

    Target assertions:
    - persists report + normalized data first
    - computes requirement guidance after persistence
    - unmet requirements produce warnings
    - final save remains non-blocking (advisory only)
    """
    raise NotImplementedError


@pytest.mark.django_db
@pytest.mark.xfail(
    reason="Needs deterministic fixture setup for versioned report objects"
)
def test_save_report_submission_expected_version_conflict_scaffold():
    """
    Scaffold for optimistic locking behavior.

    Target assertions:
    - stale expected_version raises ValidationError
    - no partial DB mutations remain after exception
    """
    raise NotImplementedError
