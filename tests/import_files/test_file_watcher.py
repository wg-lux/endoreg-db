from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from endoreg_db.models import ApplicationSettings, Center
from scripts.file_watcher import FileWatcherService


class FileWatcherServiceTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name="watch-center", display_name="Watch Center"
        )
        settings_obj = ApplicationSettings.get_solo()
        settings_obj.center = self.center
        settings_obj.save()

    def test_validate_django_setup_uses_configured_center(self) -> None:
        service = FileWatcherService()
        service._validate_django_setup()

    def test_scan_once_processes_stable_report_files(self) -> None:
        with (
            tempfile.TemporaryDirectory() as report_dir_name,
            tempfile.TemporaryDirectory() as video_dir_name,
        ):
            report_dir = Path(report_dir_name)
            report_path = report_dir / "incoming-report.pdf"
            report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

            service = FileWatcherService()
            service.report_dir = report_dir
            service.video_dir = Path(video_dir_name)
            service.stable_after_seconds = 0

            with patch(
                "scripts.file_watcher.process_watcher_file"
            ) as process_watcher_file:
                service._scan_once()

            process_watcher_file.assert_called_once_with(
                file_path=report_path,
                file_type="report",
                processor_name=None,
            )
