from django.test import TestCase
from logging import getLogger


import logging

from yaml import serialize

from endoreg_db.serializers.finding.finding import FindingSerializer, FindingClassificationSerializer

from ..helpers.data_loader import (
    load_data
)

from ..helpers.default_objects import (
    generate_patient   
)

from endoreg_db.models import Examination

BOWEL_PREP_FINDING_NAME = "bowel_preparation_simplified"
BBPS_SIMPLE_CLASSIFICATION_NAME = "bowel_prep_boston_simplified"
EXAMINATION_NAME = "colonoscopy"
CLASSIFICATION_CHOICE_NAME = "bowel_prep_boston_3"
COLON_POLYP_FINDING_NAME = "colon_polyp"

logger = getLogger(__name__)
logger.setLevel(logging.INFO) # Changed to INFO for more verbose logging during test development


class FindingSerializerTest(TestCase):
    
    # @classmethod
    # def setUpTestData(cls):
    def setUp(self):
        """
        Initializes test data for the CommonFindingTest class.
        
        Creates and retrieves Finding instances.
        """
        load_data()
        
        self.colonoscopy = Examination.objects.get(name="colonoscopy")

        self.colo_examination_indications = self.colonoscopy.indications.all()
        
        # cls.colonoscopy.findings is a ManyToManyField, so we need to filter by the examination
        self.colo_finding_bp = self.colonoscopy.findings.get(name = BOWEL_PREP_FINDING_NAME)
        self.colo_finding_polyp = self.colonoscopy.findings.get(name = COLON_POLYP_FINDING_NAME)

    def test_finding_serializer(self):
        serializer_polyp = FindingSerializer(instance=self.colo_finding_polyp)
        data = serializer_polyp.data

        self.assertIn('id', data)
        self.assertIn('name', data)
        self.assertIn('classifications', data)

        serialized_morphology_classifications = data['morphology_classifications']
        self.assertGreater(len(serialized_morphology_classifications), 0, "Serialized morphology classifications should not be empty")

        serialized_location_classifications = data['location_classifications']
        self.assertGreater(len(serialized_location_classifications), 0, "Serialized location classifications should not be empty")





   