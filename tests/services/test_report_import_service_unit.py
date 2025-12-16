# tests/services/test_report_import_service_unit.py

import unittest
from pathlib import Path

from endoreg_db.services.report_import import ReportImportService

ris = ReportImportService()
import_and_anonymize = ris.import_and_anonymize


class TestReportImportServiceUnit(unittest.TestCase):
    def test_import_and_anonymize_nonexistent_file(self):
        nonexistent_path = Path("/tmp/nonexistent_report.pdf")

        with self.assertRaises(FileNotFoundError):
            import_and_anonymize(
                file_path=nonexistent_path,
                center_name="dummy-center",
                retry=False,
            )
