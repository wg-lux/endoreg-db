from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.utils.paths import get_runtime_paths, validate_runtime_storage_contract


class Command(BaseCommand):
    help = "Validate the canonical LX_RUNTIME_ROOT topology and print resolved paths."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--json", action="store_true", help="Emit the runtime contract as JSON."
        )

    def handle(self, *args: object, **options: object) -> None:
        paths = get_runtime_paths()
        resolved_paths: dict[str, str] = {}
        for name in type(paths).model_fields:
            value: object = getattr(paths, name)
            if isinstance(value, Path) and name != "dir":
                resolved_paths[name] = str(value)
        violations: list[str] = []
        try:
            validate_runtime_storage_contract()
        except RuntimeError as exc:
            violations.append(str(exc))
        if options.get("json"):
            self.stdout.write(
                json.dumps(
                    {
                        "runtime_root": str(paths.runtime_root),
                        "paths": resolved_paths,
                        "valid": not violations,
                        "violations": violations,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            self.stdout.write(f"Runtime root: {paths.runtime_root}")
            for label, path in resolved_paths.items():
                self.stdout.write(f"- {label}: {path}")
            for violation in violations:
                self.stdout.write(self.style.ERROR(violation))
        if violations:
            raise CommandError(
                "Runtime storage contract is invalid: " + "; ".join(violations)
            )
