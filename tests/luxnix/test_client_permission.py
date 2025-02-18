from endoreg_db.models import (
    LxPermission,
)
    
from datetime import datetime

from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from .conf import (
    TEST_LX_PERMISSION_OUTPUT_PATH,
    BASE_PERMISSION_NAMES,
    ENDOREG_PERMISSION_NAMES,
)

from pathlib import Path


class TestClientType(TestCase):
    def setUp(self):
        out = StringIO()
        call_command("load_lx_data", stdout=out)

        with open(TEST_LX_PERMISSION_OUTPUT_PATH, "w") as f:
                f.write("Testing Lx Client Types\n\n")

    def test_permissions_created(self):
        with open (TEST_LX_PERMISSION_OUTPUT_PATH, "a") as f:
            f.write(f"Testing Base Permissions\n\t{BASE_PERMISSION_NAMES}\n")
            for permission_name in BASE_PERMISSION_NAMES:
                permission = LxPermission.objects.get(name=permission_name)
                self.assertTrue(permission, f"Permission {permission_name} not found")
                f.write(f"Permission {permission_name} found\n")

            f.write(f"Testing EndoReg Permissions\n\t{ENDOREG_PERMISSION_NAMES}\n")
            for permission_name in ENDOREG_PERMISSION_NAMES:
                permission = LxPermission.objects.get(name=permission_name)
                self.assertTrue(permission, f"Permission {permission_name} not found")
                f.write(f"Permission {permission_name} found\n")
