from __future__ import annotations

import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand

from endoreg_db.utils.filesystem.file_operations import safe_unlink_file
from endoreg_db.utils.filesystem.paths import QUARANTINE_DIR


class Command(BaseCommand):
    help = "Report or delete stale files from the local quarantine directory."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=30,
            help="Select quarantine files older than this many days.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report candidates without deleting them.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="Delete matching files. Without this flag the command is dry-run.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the result as JSON.",
        )

    def handle(self, *args, **options) -> None:
        older_than_days = max(0, int(options["older_than_days"]))
        dry_run = bool(options["dry_run"]) or not bool(options["confirm"])
        cutoff = time.time() - (older_than_days * 24 * 60 * 60)
        candidates = _stale_quarantine_files(
            quarantine_dir=QUARANTINE_DIR,
            cutoff=cutoff,
        )
        candidate_bytes = sum(
            path.stat().st_size for path in candidates if path.exists()
        )

        deleted: list[Path] = []
        if not dry_run:
            for path in candidates:
                safe_unlink_file(path, missing_ok=True)
                deleted.append(path)

        payload = {
            "quarantine_dir": str(QUARANTINE_DIR),
            "older_than_days": older_than_days,
            "dry_run": dry_run,
            "candidate_count": len(candidates),
            "candidate_bytes": candidate_bytes,
            "deleted_count": len(deleted),
            "candidates": [str(path) for path in candidates],
            "deleted": [str(path) for path in deleted],
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        mode = "dry-run" if dry_run else "confirmed"
        self.stdout.write(
            f"{mode}: {len(candidates)} quarantine files older than {older_than_days} days"
        )
        if deleted:
            self.stdout.write(f"deleted {len(deleted)} files")


def _stale_quarantine_files(*, quarantine_dir: Path, cutoff: float) -> list[Path]:
    if not quarantine_dir.exists():
        return []
    candidates: list[Path] = []
    for path in quarantine_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.stat().st_mtime <= cutoff:
            candidates.append(path)
    return sorted(candidates)
