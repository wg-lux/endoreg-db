# File to test the examination model

from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.finding.finding import Finding

TEST_EXAMINATION_NAME = "test_examination"


class TestExaminationModel:
    """Tests for the Examination model."""

    def test_examination_creation(self, db, django_db_setup):
        """Test creating Examination instances from default data."""
        examination = Examination.objects.create(name=TEST_EXAMINATION_NAME)
        assert examination.name == TEST_EXAMINATION_NAME
        assert Examination.objects.filter(name=TEST_EXAMINATION_NAME).exists()

    def test_add_demo_finding(self, db, new_demo_finding: "Finding"):
        """Test adding a demo finding to an examination."""
        examination = Examination.objects.create(name=TEST_EXAMINATION_NAME)
        examination.findings.add(new_demo_finding)
        _demo_finding = examination.findings.get(name=new_demo_finding.name)
        assert _demo_finding.name == new_demo_finding.name
