import importlib
import pkgutil
import unittest
from pathlib import Path

import endoreg_db  # replace with top-level package name


class TestPackageImports(unittest.TestCase):
    """
    Ensure all modules in the package can be imported
    without raising ImportError / SyntaxError / RuntimeError.
    """

    def test_import_all_modules(self):
        package_path = Path(endoreg_db.__file__).parent

        for module_info in pkgutil.walk_packages(
            [str(package_path)], prefix=endoreg_db.__name__ + "."
        ):
            module_name = module_info.name
            if module_name == "endoreg_db.config.settings.prod":
                # Production requires deployment secrets. Its import contract is
                # exercised in isolated processes by test_prod_settings_contract.py.
                continue
            with self.subTest(module=module_name):
                importlib.import_module(module_name)
