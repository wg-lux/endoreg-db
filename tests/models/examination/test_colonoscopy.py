# File to test the examination model

from endoreg_db.models.medical.examination.examination import Examination
from tests.defaults import DEFAULT_COLONOSCOPY_NAME

TEST_EXAMINATION_NAME = "test_examination"


class TestColonoscopyObject:
    """Tests for the Colonoscopy Examination object."""

    def test_colonoscopy_examination_exists(self, colonoscopy_examination: Examination):
        """Test that the colonoscopy examination object exists in the database."""
        assert colonoscopy_examination.name == DEFAULT_COLONOSCOPY_NAME
        assert Examination.objects.filter(name=DEFAULT_COLONOSCOPY_NAME).exists()

    def test_examination_types(self, colonoscopy_examination: Examination):
        """Test that the colonoscopy examination has at least one examination type."""
        assert colonoscopy_examination.examination_types.count() > 0
