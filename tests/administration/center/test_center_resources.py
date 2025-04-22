# from django.test import TestCase
# from endoreg_db.models import (
#     Center, 
#     CenterWaste,
#     CenterResource,
#     CenterProduct,
#     )

# from ...helpers.data_loader import (
#     load_
#     load_center_data,
# )

# class CenterResourceModelTest(TestCase):
#     def setUp(self):
#         # Create a Center instance for testing
#         self.center = Center.objects.create(
#             name="test_center",
#             name_de="Testzentrum",
#             name_en="Test Center"
#         )

#         # Create a CenterResource instance for testing
#         self.center_resource = CenterResource.objects.create(
#             center=self.center,
#             year=2023,
#             resource=CenterResource.objects.create(name="Test Resource"),
#             quantity=100.0,
#             unit=CenterResource.objects.create(name="Test Unit"),
#             emission_factor=CenterResource.objects.create(name="Test Emission Factor")
#         )