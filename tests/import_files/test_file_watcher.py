from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from endoreg_db.models import ApplicationSettings, Center
from scripts.file_watcher import FileWatcherService, _resolve_preanonymized_watcher_dir


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

    def test_preanonymized_dir_defaults_to_desktop_folder(self) -> None:
        expected = (Path.home() / "Desktop" / "preanonymized_import").resolve()
        assert _resolve_preanonymized_watcher_dir() == expected

    def test_preanonymized_dir_can_be_overridden_by_env(self) -> None:
        with tempfile.TemporaryDirectory() as preanonymized_dir_name:
            override_dir = Path(preanonymized_dir_name).resolve()
            with patch.dict(
                "os.environ",
                {"WATCHER_PREANONYMIZED_DIR": str(override_dir)},
                clear=False,
            ):
                assert _resolve_preanonymized_watcher_dir() == override_dir

    def test_scan_once_processes_stable_report_files(self) -> None:
        with (
            tempfile.TemporaryDirectory() as report_dir_name,
            tempfile.TemporaryDirectory() as video_dir_name,
            tempfile.TemporaryDirectory() as preanonymized_dir_name,
        ):
            report_dir = Path(report_dir_name)
            report_path = report_dir / "incoming-report.pdf"
            report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

            service = FileWatcherService()
            service.report_dir = report_dir
            service.video_dir = Path(video_dir_name)
            service.preanonymized_dir = Path(preanonymized_dir_name)
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

    def test_scan_once_processes_stable_preanonymized_files(self) -> None:
        with (
            tempfile.TemporaryDirectory() as report_dir_name,
            tempfile.TemporaryDirectory() as video_dir_name,
            tempfile.TemporaryDirectory() as preanonymized_dir_name,
        ):
            preanonymized_dir = Path(preanonymized_dir_name)
            report_path = preanonymized_dir / "preanonymized-report.pdf"
            report_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

            service = FileWatcherService()
            service.report_dir = Path(report_dir_name)
            service.video_dir = Path(video_dir_name)
            service.preanonymized_dir = preanonymized_dir
            service.stable_after_seconds = 0

            with patch(
                "scripts.file_watcher.process_preanonymized_watcher_file"
            ) as process_preanonymized:
                service._scan_once()

            process_preanonymized.assert_called_once_with(file_path=report_path)
