from endoreg_db.models import (
    LxClientType,
)
    
from datetime import datetime

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_LX_CLIENT_TYPE_OUTPUT_PATH,
    CLIENT_TYPE_NAME_ABBREVIATION_TUPLES,
)



class TestClientType(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_lx_data", stdout=out)

        with open(TEST_LX_CLIENT_TYPE_OUTPUT_PATH, "w") as f:
                f.write("Testing Lx Client Types\n\n")

    def test_client_types_created(self):
        with open (TEST_LX_CLIENT_TYPE_OUTPUT_PATH, "a") as f:
            for client_type_name, abbreviation in CLIENT_TYPE_NAME_ABBREVIATION_TUPLES:
                f.write(f"Testing Client Type {client_type_name}\n")
                client_type = LxClientType.objects.get(name=client_type_name)
                self.assertTrue(client_type, f"Client Type {client_type_name} not found")
                self.assertTrue(client_type.abbreviation == abbreviation, f"Client Type {client_type_name} abbreviation not found")
                f.write(f"Client Type {client_type_name} found\n")

