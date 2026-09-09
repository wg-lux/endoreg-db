import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Literal

from django.db import OperationalError, ProgrammingError, transaction

from endoreg_db.config.env import reconciliation_disabled
from endoreg_db.import_files.context.file_lock import STALE_LOCK_SECONDS
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.media_integrity import reconcile_media_integrity
from endoreg_db.services.streamable_media import (
    STREAMABLE_PROCESSED_VIDEO_ROOT,
    STREAMABLE_RAW_VIDEO_ROOT,
    STREAMABLE_VIDEO_ROOT,
    sync_video_streamable_artifacts,
)
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    ensure_directory,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.paths import data_paths
from endoreg_db.utils.storage import file_exists, save_local_file

logger = logging.getLogger(__name__)

_reconciliation_ran = False


class ReconciliationService:
    """Startup repair service for media-processing files and state.

    The service is intentionally conservative: it only repairs well-understood
    inconsistencies that can be inferred from on-disk artifacts and persisted
    processing state. It is meant to run once when a runtime process starts so
    interrupted imports do not leave the application stuck behind stale locks,
    broken raw-file links, or permanently "processing" state flags.
    """

    lock_filename = ".reconciliation.lock"
    artifact_stale_seconds = STALE_LOCK_SECONDS

    def run_once(self) -> None:
        """Run reconciliation at most once per process.

        The method short-circuits when reconciliation has already completed in
        this process, when it is explicitly disabled via environment variable,
        or when another process is already holding the startup lock.

        Database bootstrap races are tolerated: if the schema is not ready yet,
        reconciliation is skipped and a warning is logged instead of crashing
        the process during startup.
        """

        global _reconciliation_ran
        if _reconciliation_ran:
            return
        if reconciliation_disabled():
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
        """Execute the full recovery pass in a deterministic order."""

        self.clear_stale_lock_files()
        self.relink_broken_video_raw_files()
        self.cleanup_orphaned_artifacts()
        self.reset_incomplete_processing_states()
        reconcile_media_integrity()

    def clear_stale_lock_files(self) -> int:
        """Retain local lock files; age is not authoritative ownership evidence."""

        for root in (data_paths["import_video"], data_paths["import_report"]):
            for lock_path in Path(root).glob("*.lock"):
                logger.warning(
                    "Retaining local import lock during reconciliation because "
                    "database lease state is authoritative: %s",
                    lock_path,
                )
        return 0

    def cleanup_orphaned_artifacts(self, *, dry_run: bool = False) -> int:
        """Classify apparent staging artifacts without deleting unknown ownership."""

        removed = 0
        reproducible_cache_roots = {
            STREAMABLE_VIDEO_ROOT,
            STREAMABLE_RAW_VIDEO_ROOT,
            STREAMABLE_PROCESSED_VIDEO_ROOT,
        }
        scan_dirs = (
            Path(data_paths["sensitive_video"]),
            Path(data_paths["anonym_video"]),
            Path(data_paths["transcoding"]),
            STREAMABLE_VIDEO_ROOT,
            STREAMABLE_RAW_VIDEO_ROOT,
            STREAMABLE_PROCESSED_VIDEO_ROOT,
        )
        for root in scan_dirs:
            if not root.exists():
                continue
            for path in root.iterdir():
                if not path.is_file():
                    continue
                if not self._is_cleanup_candidate(path):
                    continue
                if not self._is_stale(path, self.artifact_stale_seconds):
                    continue
                if root in reproducible_cache_roots:
                    if not dry_run:
                        safe_unlink_file(path, missing_ok=True)
                    removed += 1
                    logger.warning(
                        "%s stale unpublished streamable-cache artifact: %s",
                        "Would remove" if dry_run else "Removed",
                        path,
                    )
                    continue
                logger.warning(
                    "Retaining apparent orphaned artifact because no durable "
                    "attempt generation proves cleanup ownership: %s",
                    path,
                )
        return removed

    def relink_broken_video_raw_files(self) -> int:
        """Repair `VideoFile.raw_file` pointers whose referenced file is missing.

        The service first looks for deterministic candidates such as the
        canonical `<video_hash>.<suffix>` name and then falls back to content
        hash matches in the sensitive video directory. Ambiguous matches are
        skipped rather than guessed.
        """

        recovered = 0
        sensitive_dir = Path(data_paths["sensitive_video"])
        unresolved: list[VideoFile] = []
        claimed_hashes: set[str] = set()

        for video in VideoFile.objects.filter(raw_file__isnull=False).exclude(
            raw_file=""
        ):
            if not file_exists(getattr(video, "raw_file", None)):
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

            final_path, relative_name = self._promote_video_raw_candidate(
                video=video,
                candidate=candidate,
                sensitive_dir=sensitive_dir,
            )
            if final_path is None:
                continue

            with transaction.atomic():
                video.raw_file.name = relative_name
                video.save(update_fields=["raw_file"])
            try:
                sync_video_streamable_artifacts(
                    video,
                    include_raw=True,
                    include_processed=False,
                    save=True,
                )
            except Exception as exc:
                logger.warning(
                    "Could not synchronize reconciled raw streamable artifact for video %s: %s",
                    video.video_hash,
                    exc,
                )

            recovered += 1
            claimed_hashes.add(video_hash)
            logger.warning(
                "Relinked raw file for video %s to %s",
                video.video_hash,
                final_path,
            )

        return recovered

    def reset_incomplete_processing_states(self) -> int:
        """Retain ambiguous legacy processing states for fenced recovery.

        A media state is not currently linked to the exact import-attempt
        generation that created it. Resetting it from process startup would let
        an old process race a current database owner. The reconciliation pass
        therefore reports these rows and leaves recovery to the attempt-aware
        upload/report services. Once generation linkage is persisted, this
        method may auto-repair only a row whose lease and fencing token are
        proved current in the same database transaction.
        """

        video_states = VideoState.objects.select_related("video_file").filter(
            processing_started=True,
            sensitive_meta_processed=False,
        )
        for state in video_states:
            logger.warning(
                "Retaining incomplete video processing state for fenced recovery: %s",
                getattr(getattr(state, "video_file", None), "video_hash", None),
            )

        pdf_states = RawPdfState.objects.select_related("raw_pdf_file").filter(
            processing_started=True,
            sensitive_meta_processed=False,
        )
        for state in pdf_states:
            logger.warning(
                "Retaining incomplete report processing state for fenced recovery: %s",
                getattr(getattr(state, "raw_pdf_file", None), "pdf_hash", None),
            )
        return 0

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
        raw_name, canonical_path = self._raw_candidate_names(
            video=video,
            sensitive_dir=sensitive_dir,
        )
        content_matches = hashed_candidates.get(str(video.video_hash), [])
        competing_content_matches = [
            path
            for path in content_matches
            if canonical_path is None or path != canonical_path
        ]

        if canonical_path is not None and canonical_path.is_file():
            return self._resolve_existing_canonical_candidate(
                video=video,
                raw_name=raw_name,
                canonical_path=canonical_path,
                competing_content_matches=competing_content_matches,
            )
        deterministic_candidate = self._first_deterministic_raw_candidate(
            raw_name=raw_name,
            canonical_path=canonical_path,
            sensitive_dir=sensitive_dir,
        )
        if deterministic_candidate is not None:
            return deterministic_candidate
        return self._unique_content_hash_candidate(video, content_matches)

    def _raw_candidate_names(
        self,
        *,
        video: VideoFile,
        sensitive_dir: Path,
    ) -> tuple[str | None, Path | None]:
        raw_file_name = getattr(video.raw_file, "name", None)
        raw_name = Path(raw_file_name).name if raw_file_name else None
        suffix = Path(raw_name).suffix if raw_name else (video.suffix or ".mp4")
        canonical_path = (
            sensitive_dir / f"{video.video_hash}{suffix}" if suffix else None
        )
        return raw_name, canonical_path

    def _resolve_existing_canonical_candidate(
        self,
        *,
        video: VideoFile,
        raw_name: str | None,
        canonical_path: Path,
        competing_content_matches: list[Path],
    ) -> Path | None:
        if raw_name == canonical_path.name:
            return canonical_path
        if not competing_content_matches:
            return canonical_path
        logger.warning(
            "Skipping relink for video %s because canonical path %s exists but competing content-hash candidates were also found: %s",
            video.video_hash,
            canonical_path,
            [str(path) for path in competing_content_matches],
        )
        return None

    def _first_deterministic_raw_candidate(
        self,
        *,
        raw_name: str | None,
        canonical_path: Path | None,
        sensitive_dir: Path,
    ) -> Path | None:
        candidates = [canonical_path] if canonical_path is not None else []
        if raw_name:
            candidates.append(sensitive_dir / raw_name)
        seen: set[Path] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                return candidate
        return None

    def _unique_content_hash_candidate(
        self,
        video: VideoFile,
        content_matches: list[Path],
    ) -> Path | None:
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
    ) -> tuple[Path | None, str]:
        raw_file_name = getattr(video.raw_file, "name", None)
        raw_name = Path(raw_file_name).name if raw_file_name else None
        suffix = (
            Path(raw_name).suffix
            if raw_name
            else (video.suffix or candidate.suffix or ".mp4")
        )
        canonical_path = sensitive_dir / f"{video.video_hash}{suffix}"
        relative_name = str(Path(sensitive_dir.name) / canonical_path.name)

        if candidate == canonical_path:
            return self._store_raw_candidate(
                video=video,
                candidate=candidate,
                relative_name=relative_name,
            )

        if canonical_path.exists():
            logger.warning(
                "Cannot relink video %s because canonical path already exists: %s",
                video.video_hash,
                canonical_path,
            )
            return None, relative_name

        raw_file = getattr(video, "raw_file", None)
        if raw_file is not None and getattr(raw_file, "storage", None) is not None:
            return self._store_raw_candidate(
                video=video,
                candidate=candidate,
                relative_name=relative_name,
            )

        ensure_directory(canonical_path.parent)
        atomic_move_file(source=candidate, destination=canonical_path)
        return canonical_path, relative_name

    def _store_raw_candidate(
        self,
        *,
        video: VideoFile,
        candidate: Path,
        relative_name: str,
    ) -> tuple[Path | None, str]:
        raw_file = getattr(video, "raw_file", None)
        storage = getattr(raw_file, "storage", None)
        if raw_file is None or storage is None:
            return candidate, relative_name

        storage_path = None
        try:
            storage_path = Path(storage.path(relative_name)).resolve()
        except Exception:
            storage_path = None

        repair_plaintext_file = getattr(storage, "repair_plaintext_file", None)
        if storage_path is not None and candidate.resolve() == storage_path:
            if callable(repair_plaintext_file):
                repair_plaintext_file(relative_name)
                raw_file.name = relative_name
                return candidate, relative_name

            temp_source = candidate.with_name(
                f".{candidate.name}.reconcile-source.{os.getpid()}"
            )
            atomic_copy_file(source=candidate, destination=temp_source)
            try:
                save_local_file(
                    raw_file,
                    temp_source,
                    name=relative_name,
                    save=False,
                    overwrite=True,
                )
            finally:
                safe_unlink_file(temp_source, missing_ok=True)
            return storage_path, relative_name

        save_local_file(
            raw_file,
            candidate,
            name=relative_name,
            save=False,
            overwrite=True,
        )
        safe_unlink_file(candidate, missing_ok=True)
        raw_file.name = relative_name
        return storage_path or candidate, relative_name

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
        self.fd: int | None = None

    def __enter__(self) -> "_exclusive_lock":
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(self.fd, str(os.getpid()).encode("ascii"))
        os.close(self.fd)
        self.fd = None
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> Literal[False]:
        if self.fd is not None:
            os.close(self.fd)
        safe_unlink_file(self.path, missing_ok=True)
        return False


def should_run_startup_reconciliation(argv: list[str] | None = None) -> bool:
    """Return whether reconciliation should attach for the current entrypoint.

    Reconciliation is runtime recovery logic, not migration or management
    command logic. The startup hook therefore runs only for server-style
    entrypoints and is suppressed for pytest and one-off management commands
    such as `migrate` or `load_base_db_data`.
    """

    argv = argv or sys.argv
    executable = Path(argv[0]).name if argv else ""
    if (
        "pytest" in executable
        or executable == "py.test"
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        return False
    if len(argv) < 2:
        return False

    runtime_commands = {
        "runserver",
        "run_gunicorn",
        "gunicorn",
        "uvicorn",
        "daphne",
    }
    return argv[1] in runtime_commands
