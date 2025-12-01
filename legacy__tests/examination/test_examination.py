from endoreg_db.models import Examination, Finding
from endoreg_db.serializers.examination.base import ExaminationSerializer
from django.test import TestCase
from ..helpers.data_loader import load_data

EXAMINATION_COLONOSCOPY_NAME="colonoscopy"
FINDING_COLON_POLYP_NAME="colon_polyp"

class ExaminationTest(TestCase):
    def setUp(self):
        """
        Initializes test data for the ExaminationTest class.
        
        Creates and retrieves Examination instances.
        """

        load_data()

        self.examination_colonoscopy = Examination.objects.get(name=EXAMINATION_COLONOSCOPY_NAME)
        self.finding_colon_polyp = Finding.objects.get(name=FINDING_COLON_POLYP_NAME)

    def test_examination_colonoscopy(self):
        colonoscopy = self.examination_colonoscopy
        self.assertIsNotNone(colonoscopy, "Colonoscopy examination should not be None")
        self.assertEqual(colonoscopy.name, EXAMINATION_COLONOSCOPY_NAME, "Colonoscopy examination name should match the expected value")
        self.assertTrue(colonoscopy.findings.exists(), "Colonoscopy examination should have associated findings")

        polyp = colonoscopy.findings.get(name=FINDING_COLON_POLYP_NAME)
        self.assertIsNotNone(polyp, "Colonoscopy should have a finding named 'colon_polyp'")
        self.assertEqual(polyp.name, FINDING_COLON_POLYP_NAME, "Finding name should match the expected value")
        self.assertIn(self.finding_colon_polyp, colonoscopy.findings.all(), "Colonoscopy should include the 'colon_polyp' finding in its findings list")

    def test_examination_serializer(self):
        serializer = ExaminationSerializer(instance=self.examination_colonoscopy)
        data = serializer.data

        self.assertEqual(data['name'], EXAMINATION_COLONOSCOPY_NAME)
        self.assertIn('findings', data)
        serialized_findings = data['findings']
        serialized_finding_names = [finding['name'] for finding in serialized_findings]
        self.assertIn(FINDING_COLON_POLYP_NAME, serialized_finding_names, "Serialized findings should include 'colon_polyp' finding")
        