from __future__ import annotations

from pathlib import Path
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.runtime_wheel_staging import (
    DEFAULT_MAX_RUNTIME_ROOT_ENTRIES,
    reap_runtime_wheel_staging,
)


class Command(BaseCommand):
    help = (
        "Inventory or remove obsolete top-level LX-Annotate deployment wheel "
        "staging files. Dry-run is the default."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--runtime-root", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--keep-name", action="append", default=[])
        parser.add_argument(
            "--max-entries",
            type=int,
            default=DEFAULT_MAX_RUNTIME_ROOT_ENTRIES,
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            runtime_root = cast(str, options["runtime_root"])
            keep_names = cast(list[str], options["keep_name"])
            max_entries = cast(int, options["max_entries"])
            result = reap_runtime_wheel_staging(
                runtime_root=Path(runtime_root),
                apply=bool(options["apply"]),
                keep_names=frozenset(keep_names),
                max_entries=max_entries,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(result.model_dump_json())
