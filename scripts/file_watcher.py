from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist

from endoreg_db.services.hub import process_watcher_file, resolve_default_center
from endoreg_db.utils.defaults.set_default_center import get_default_processor
from endoreg_db.utils.paths import IMPORT_REPORT_DIR, IMPORT_VIDEO_DIR

logger = logging.getLogger(__name__)


class FileWatcherService:
    def __init__(self) -> None:
        self.video_dir = IMPORT_VIDEO_DIR
        self.report_dir = IMPORT_REPORT_DIR
        self.poll_interval_seconds = float(
            os.environ.get("WATCHER_POLL_INTERVAL_SECONDS", "5")
        )
        self.stable_after_seconds = float(
            os.environ.get("WATCHER_STABLE_AFTER_SECONDS", "10")
        )
        self.processed_files: set[str] = set()

    def _validate_django_setup(self) -> None:
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        if resolve_default_center() is None:
            raise ObjectDoesNotExist(
                "No center is configured for watcher ingestion. Configure ApplicationSettings.center or create a center."
            )

    def _process_existing_files(self) -> None:
        self._validate_django_setup()
        self._scan_once()

    def start(self) -> None:
        self._validate_django_setup()
        while True:
            self._scan_once()
            time.sleep(self.poll_interval_seconds)

    def _scan_once(self) -> None:
        for file_path in self._iter_candidates(self.report_dir, suffixes={".pdf", ".txt"}):
            self._process_candidate(file_path=file_path, file_type="report")
        for file_path in self._iter_candidates(
            self.video_dir,
            suffixes={".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"},
        ):
            self._process_candidate(file_path=file_path, file_type="video")

    def _iter_candidates(self, directory: Path, *, suffixes: set[str]) -> list[Path]:
        if not directory.exists():
            return []
        candidates: list[Path] = []
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in suffixes:
                continue
            if self._is_stable(path):
                candidates.append(path)
        return candidates

    def _is_stable(self, file_path: Path) -> bool:
        try:
            stat_result = file_path.stat()
        except FileNotFoundError:
            return False
        age_seconds = time.time() - stat_result.st_mtime
        return age_seconds >= self.stable_after_seconds

    def _process_candidate(self, *, file_path: Path, file_type: str) -> None:
        signature = self._build_signature(file_path)
        if signature in self.processed_files:
            return

        logger.info("Watcher processing %s file %s", file_type, file_path)
        try:
            processor_name = None
            if file_type == "video":
                processor = get_default_processor()
                processor_name = getattr(processor, "name", None)
            process_watcher_file(
                file_path=file_path,
                file_type=file_type,
                processor_name=processor_name,
            )
            self.processed_files.add(signature)
        except FileNotFoundError:
            logger.info("Watcher candidate disappeared before processing: %s", file_path)
        except Exception:
            logger.exception("Watcher failed to process %s", file_path)

    @staticmethod
    def _build_signature(file_path: Path) -> str:
        stat_result = file_path.stat()
        return f"{file_path}:{int(stat_result.st_mtime_ns)}:{stat_result.st_size}"
