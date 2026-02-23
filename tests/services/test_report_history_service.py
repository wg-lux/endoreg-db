from __future__ import annotations

import pytest


@pytest.mark.django_db
@pytest.mark.xfail(reason="Needs compact fixtures for patient/patient_examination/patient_findings")
def test_get_patient_examination_history_context_excludes_current_exam_scaffold():
    """
    Scaffold for report history service.

    Target assertions:
    - current examination is excluded
    - prior examinations are ordered descending
    - only active findings are included
    - nested classifications/interventions are serialized
    """
    raise NotImplementedError

