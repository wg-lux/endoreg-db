import logging
import os
import sys
import time
import uuid
from pathlib import Path

from django.db import OperationalError, ProgrammingError, transaction

from endoreg_db.import_files.context.file_lock import STALE_LOCK_SECONDS
from endoreg_db.models import VideoFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.paths import data_paths

logger = logging.getLogger(__name__)

_reconciliation_ran = False


class ReconciliationService:
    lock_filename = ".reconciliation.lock"
    artifact_stale_seconds = STALE_LOCK_SECONDS

    def run_once(self) -> None:
        global _reconciliation_ran
        if _reconciliation_ran:
            return
        if os.getenv("ENDOREG_DISABLE_RECONCILIATION") == "1":
            return

        try:
            with self._startup_lock():
                self.run()
                _reconciliation_ran = True
        except FileExistsError:
            logger.info("Reconciliation already running in another process; skipping.")
        except (OperationalError, ProgrammingError) as exc:
            logger.warning("Skipping reconciliation during startup: %s", exc)

    def run(self) -> None:
        self.clear_stale_lock_files()
        self.relink_broken_video_raw_files()
        self.cleanup_orphaned_artifacts()
        self.reset_incomplete_processing_states()

    def clear_stale_lock_files(self) -> int:
        now = self._now()
        removed = 0
        for root in (data_paths["import_video"], data_paths["import_report"]):
            for lock_path in Path(root).glob("*.lock"):
                try:
                    age = now - lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > STALE_LOCK_SECONDS:
                    lock_path.unlink(missing_ok=True)
                    removed += 1
                    logger.warning("Removed stale lock file: %s", lock_path)
        return removed

    def cleanup_orphaned_artifacts(self) -> int:
        removed = 0
        scan_dirs = (
            data_paths["sensitive_video"],
            data_paths["anonym_video"],
            data_paths["transcoding"],
        )
        for root in scan_dirs:
            for path in Path(root).iterdir():
                if not path.is_file():
                    continue
                if not self._is_cleanup_candidate(path):
                    continue
                if not self._is_stale(path, self.artifact_stale_seconds):
                    continue
                path.unlink(missing_ok=True)
                removed += 1
                logger.warning("Removed orphaned startup artifact: %s", path)
        return removed

    def relink_broken_video_raw_files(self) -> int:
        recovered = 0
        sensitive_dir = Path(data_paths["sensitive_video"])
        storage_root = Path(data_paths["storage"])
        unresolved = []
        claimed_hashes: set[str] = set()

        for video in VideoFile.objects.filter(raw_file__isnull=False).exclude(
            raw_file=""
        ):
            if video.get_raw_file_path() is None:
                unresolved.append(video)

        if not unresolved:
            return 0

        hashed_candidates = self._build_content_hash_index(
            sensitive_dir=sensitive_dir,
            target_hashes={str(video.video_hash) for video in unresolved},
        )

        for video in unresolved:
            video_hash = str(video.video_hash)
            if video_hash in claimed_hashes:
                logger.warning(
                    "Skipping relink for video %s because that hash was already claimed in this reconciliation run.",
                    video.video_hash,
                )
                continue

            candidate = self._resolve_video_raw_candidate(
                video=video,
                sensitive_dir=sensitive_dir,
                hashed_candidates=hashed_candidates,
            )
            if candidate is None:
                continue

            final_path = self._promote_video_raw_candidate(
                video=video,
                candidate=candidate,
                sensitive_dir=sensitive_dir,
            )
            if final_path is None:
                continue

            try:
                relative_name = str(final_path.relative_to(storage_root))
            except ValueError:
                relative_name = str(Path(sensitive_dir.name) / final_path.name)

            with transaction.atomic():
                video.raw_file.name = relative_name
                video.save(update_fields=["raw_file"])

            recovered += 1
            claimed_hashes.add(video_hash)
            logger.warning(
                "Relinked raw file for video %s to %s",
                video.video_hash,
                final_path,
            )

        return recovered

    def reset_incomplete_processing_states(self) -> int:
        reset = 0

        video_states = VideoState.objects.select_related("video_file").filter(
            processing_started=True,
            sensitive_meta_processed=False,
        )
        for state in video_states:
            with transaction.atomic():
                state.mark_processing_not_started()
                video = getattr(state, "video_file", None)
                if video is not None:
                    ProcessingHistory.mark_failure(
                        file_hash=video.video_hash,
                        obj=video,
                    )
            reset += 1
            logger.warning(
                "Reset incomplete video processing state for video %s",
                getattr(getattr(state, "video_file", None), "video_hash", None),
            )

        pdf_states = RawPdfState.objects.select_related("raw_pdf_file").filter(
            processing_started=True,
            sensitive_meta_processed=False,
        )
        for state in pdf_states:
            with transaction.atomic():
                state.mark_processing_not_started()
                raw_pdf = getattr(state, "raw_pdf_file", None)
                if raw_pdf is not None:
                    ProcessingHistory.mark_failure(
                        file_hash=raw_pdf.pdf_hash,
                        obj=raw_pdf,
                    )
            reset += 1
            logger.warning(
                "Reset incomplete report processing state for pdf %s",
                getattr(getattr(state, "raw_pdf_file", None), "pdf_hash", None),
            )

        return reset

    def _is_cleanup_candidate(self, path: Path) -> bool:
        return (
            path.suffix in {".tmp", ".part"}
            or ".tmp." in path.name
            or ".part." in path.name
            or self._looks_like_uuid_filename(path)
        )

    def _resolve_video_raw_candidate(
        self,
        video: VideoFile,
        sensitive_dir: Path,
        hashed_candidates: dict[str, list[Path]],
    ) -> Path | None:
        raw_name = (
            Path(video.raw_file.name).name
            if getattr(video.raw_file, "name", None)
            else None
        )
        suffix = Path(raw_name).suffix if raw_name else (video.suffix or ".mp4")
        canonical_path = (
            sensitive_dir / f"{video.video_hash}{suffix}" if suffix else None
        )
        content_matches = hashed_candidates.get(str(video.video_hash), [])
        competing_content_matches = [
            path
            for path in content_matches
            if canonical_path is None or path != canonical_path
        ]

        if canonical_path and canonical_path.is_file():
            if raw_name == canonical_path.name:
                return canonical_path
            if competing_content_matches:
                logger.warning(
                    "Skipping relink for video %s because canonical path %s exists but competing content-hash candidates were also found: %s",
                    video.video_hash,
                    canonical_path,
                    [str(path) for path in competing_content_matches],
                )
                return None
            return canonical_path

        deterministic_candidates: list[Path] = []
        if canonical_path:
            deterministic_candidates.append(canonical_path)
        if raw_name:
            deterministic_candidates.append(sensitive_dir / raw_name)

        seen = set()
        for candidate in deterministic_candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                return candidate

        if len(content_matches) == 1:
            return content_matches[0]
        if len(content_matches) > 1:
            logger.warning(
                "Multiple content-hash candidates found for video %s: %s",
                video.video_hash,
                [str(path) for path in content_matches],
            )
        return None

    def _promote_video_raw_candidate(
        self,
        video: VideoFile,
        candidate: Path,
        sensitive_dir: Path,
    ) -> Path | None:
        raw_name = (
            Path(video.raw_file.name).name
            if getattr(video.raw_file, "name", None)
            else None
        )
        suffix = (
            Path(raw_name).suffix
            if raw_name
            else (video.suffix or candidate.suffix or ".mp4")
        )
        canonical_path = sensitive_dir / f"{video.video_hash}{suffix}"

        if candidate == canonical_path:
            return candidate

        if canonical_path.exists():
            logger.warning(
                "Cannot relink video %s because canonical path already exists: %s",
                video.video_hash,
                canonical_path,
            )
            return None

        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate, canonical_path)
        return canonical_path

    def _build_content_hash_index(
        self, sensitive_dir: Path, target_hashes: set[str]
    ) -> dict[str, list[Path]]:
        matches: dict[str, list[Path]] = {
            target_hash: [] for target_hash in target_hashes
        }
        if not target_hashes or not sensitive_dir.exists():
            return matches

        for path in sensitive_dir.iterdir():
            if not path.is_file() or self._should_skip_recovery_candidate(path):
                continue
            try:
                file_hash = sha256_file(path)
            except OSError as exc:
                logger.warning("Could not hash recovery candidate %s: %s", path, exc)
                continue
            if file_hash in matches:
                matches[file_hash].append(path)

        return matches

    def _should_skip_recovery_candidate(self, path: Path) -> bool:
        return (
            path.name.startswith(".")
            or path.suffix in {".tmp", ".part", ".lock"}
            or ".tmp." in path.name
            or ".part." in path.name
            or self._looks_like_uuid_filename(path)
        )

    def _looks_like_uuid_filename(self, path: Path) -> bool:
        try:
            uuid.UUID(path.stem)
            return True
        except ValueError:
            return False

    def _now(self) -> float:
        return time.time()

    def _is_stale(self, path: Path, seconds: int) -> bool:
        try:
            return (self._now() - path.stat().st_mtime) > seconds
        except FileNotFoundError:
            return False

    def _startup_lock(self):
        lock_path = data_paths["storage"] / self.lock_filename
        return _exclusive_lock(lock_path)


class _exclusive_lock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(self.fd, str(os.getpid()).encode("ascii"))
        os.close(self.fd)
        self.fd = None
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)
        return False


def should_run_startup_reconciliation(argv: list[str] | None = None) -> bool:
    argv = argv or sys.argv
    executable = Path(argv[0]).name if argv else ""
    if (
        "pytest" in executable
        or executable == "py.test"
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        return False
    if len(argv) < 2:
        return True
    blocked = {
        "makemigrations",
        "migrate",
        "collectstatic",
        "shell",
        "dbshell",
        "test",
        "pytest",
    }
    return argv[1] not in blocked
