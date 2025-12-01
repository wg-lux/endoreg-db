import pytest
from django.core.management import call_command

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding

from .constants import (
    DEFAULT_CENTER_NAME,
    DEFAULT_COLONOSCOPY_NAME,
    DEFAULT_SEGMENTATION_MODEL_NAME,
)


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """
    Set up the test database for the session.
    Since we're using in-memory SQLite, this is minimal.
    """
    with django_db_blocker.unblock():
        call_command(
            "load_base_db_data",
        )


# Fixture to fetch Examination object named "colonoscopy" from the database
@pytest.fixture(scope="function")
def colonoscopy_examination(db) -> Examination:
    """
    Retrieve the Examination object for "colonoscopy".
    Depends on base_db_data fixture to ensure data is loaded.
    """
    from endoreg_db.models import Examination

    examination = Examination.objects.get(name=DEFAULT_COLONOSCOPY_NAME)
    assert examination is not None
    return examination


@pytest.fixture(scope="function")
def new_demo_finding(db) -> Finding:
    """
    Create and return a new demo Finding object for testing.
    """
    finding = Finding.objects.create(name="Demo Finding")
    return finding
