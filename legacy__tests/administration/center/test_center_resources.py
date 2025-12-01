from django.test import TestCase

from endoreg_db.models import (
    Center,
    CenterResource,
    EmissionFactor,
    Resource,
    Unit,
)


class CenterResourceModelTest(TestCase):
    def setUp(self):
        # Create a Center instance for testing
        self.center = Center.objects.create(name="test_center")
        self.unit = Unit.objects.create(name="Test Unit")
        self.ef = EmissionFactor.objects.create(name="Test Emission Factor", value=10)
        self.center_resource = CenterResource.objects.create(
            center=self.center,
            year=2023,
            resource=Resource.objects.create(
                name="Test Resource",
            ),
            quantity=100.0,
            unit=self.unit,
            use_emission_factor=self.ef,
        )

    def test_center_resource_display_str(self):
        expected_str = (
            f"{self.center_resource.name}:\n"
            f"\tCenter\t-\t{self.center_resource.center}\n"
            f"\tResource\t-\t{self.center_resource.resource}\n"
            f"\tQuantity\t-\t{self.center_resource.quantity}\n"
            f"\tYear\t-\t{self.center_resource.year}\n"
            f"\tUnit\t-\t{self.center_resource.unit}\n"
            f"\tUse Emission Factor\t-\t{self.center_resource.use_emission_factor}\n\n"
        )
        display_str = self.center_resource.display_str()
        self.assertEqual(display_str, expected_str)

    def test_center_resource_str(self):
        name = self.center_resource.name if self.center_resource.name else "No Name"
        expected_str = f"CenterResource {self.center_resource.pk} - {name}"
        self.assertEqual(str(self.center_resource), expected_str)
