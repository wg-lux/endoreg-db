from __future__ import annotations

import pytest


@pytest.mark.django_db
@pytest.mark.xfail(reason="Needs compact requirement-set fixtures for deterministic evaluation")
def test_evaluate_patient_exam_requirement_guidance_returns_advisory_payload_scaffold():
    """
    Scaffold for advisory requirement guidance helper.

    Target assertions:
    - returns stable keys (`requirement_status`, `requirement_set_status`, `suggested_actions`, ...)
    - `advisory_only` is True
    - selected requirement set IDs override prior narrowing
    """
    raise NotImplementedError

