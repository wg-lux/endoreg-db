from endoreg_db.models import (
    Endoscope,
    EndoscopyProcessor
)

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import TEST_CENTER_NAME, TEST_ENDOSCOPE_OUTPUT_PATH, TEST_GREEN_ENDOSCOPY_OUTPUT_PATH


class TestHardware(TestCase):
    def setUp(self):
        # Load required data
        out = StringIO()
        call_command("load_gender_data", stdout=out)
        call_command("load_unit_data", stdout=out)
        call_command("load_name_data", stdout=out)
        call_command("load_center_data", stdout=out)
        call_command("load_endoscope_data", stdout=out)
        call_command("load_green_endoscopy_wuerzburg_data", stdout=out)

        with open(TEST_ENDOSCOPE_OUTPUT_PATH, "w") as f:
            f.write("Test Endoscopes\n")

        with open(TEST_GREEN_ENDOSCOPY_OUTPUT_PATH, "w") as f:
            f.write("Test Green Endoscopy Data\n")

    def write_endoscopes_created(self, f):
        endoscopes = Endoscope.objects.all()

        for e in endoscopes:
            f.write(f"{e} (sn: {e.sn}, center: {e.center})\n")

    def write_endoscopy_processors_created(self, f):
        endoscopy_processors = EndoscopyProcessor.objects.all()

        for ep in endoscopy_processors:
            f.write(f"{ep}\n")

    def write_green_endoscopy_data(self, model, f):
        objects = model.objects.all()

        f.write(f"\nChecking {model.__name__}\n")
        for obj in objects:
            f.write(f"{obj}\n")

        

    def test_endoscopy_data(self):
        with open(TEST_ENDOSCOPE_OUTPUT_PATH, "a") as f:
            self.write_endoscopes_created(f)
            self.write_endoscopy_processors_created(f)

    def test_green_endoscopy_data(self):
        from endoreg_db.models import (
            EmissionFactor, 
            Resource,
            Waste,
            Material,
            ProductGroup,
            TransportRoute,
            Product,
            ReferenceProduct,
            CenterWaste,
            CenterResource,
            ProductMaterial,
            ProductWeight,

        )
        models = [
            EmissionFactor, 
            Resource,
            Waste,
            Material,
            ProductGroup,
            TransportRoute,
            Product,
            ReferenceProduct,
            CenterWaste,
            CenterResource,
            ProductMaterial,
            ProductWeight
        ]

        with open(TEST_GREEN_ENDOSCOPY_OUTPUT_PATH, "a") as f:
            for model in models:
                self.write_green_endoscopy_data(model, f)


