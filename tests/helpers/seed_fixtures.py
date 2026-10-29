"""Narrow seed fixtures shared by directory conftests.

The global ``base_db_data`` fixture reloads the full seed catalogue inside every
test transaction (about five seconds each). Most tests only depend on the rows
below, so they request exactly those. Import these fixtures into a directory
``conftest.py``; tests that exercise the catalogue itself keep ``base_db_data``.
"""

from __future__ import annotations

import pytest

from endoreg_db.models import Center, EndoscopyProcessor
from tests.helpers.data_loader import (
    load_center_data,
    load_endoscope_data,
    load_gender_data,
)


@pytest.fixture
def gender_data(db: object) -> None:
    """Gender rows required for identity hashing and pseudonym generation."""
    del db
    load_gender_data()


@pytest.fixture
def default_center(db: object) -> Center:
    """A configured center loaded from the shipped center data."""
    del db
    load_center_data()
    center = Center.objects.order_by("pk").first()
    if center is None:
        raise RuntimeError("load_center_data created no Center rows")
    return center


@pytest.fixture
def default_processor(db: object) -> EndoscopyProcessor:
    """A configured endoscopy processor loaded from the shipped catalogue."""
    del db
    load_endoscope_data()
    processor = EndoscopyProcessor.objects.order_by("pk").first()
    if processor is None:
        raise RuntimeError("load_endoscope_data created no EndoscopyProcessor rows")
    return processor
