"""Operator adapter for portable, validated terminology artifacts."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from endoreg_db.services.reference_data.package_deployment import (
    build_terminology_package,
    export_terminology_package,
    import_terminology_package,
)


class Command(BaseCommand):
    help = (
        "Import ZIP, build folder, or export ZIP using lx-dtypes validation. "
        "Artifacts are immutable; registry activation remains explicit."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("operation", choices=("import", "build", "export"))
        parser.add_argument("source", type=Path, help="ZIP or package config.yaml")
        parser.add_argument(
            "name",
            help="New folder under runtime terminology, or filename under runtime export",
        )

    def handle(self, *args: str, **options: object) -> None:
        source, name = options["source"], options["name"]
        if not isinstance(source, Path) or not isinstance(name, str):
            raise CommandError("Source path and artifact name are required")
        try:
            operation = options["operation"]
            if operation == "import":
                destination = import_terminology_package(source, name=name)
            elif operation == "build":
                destination = build_terminology_package(source, name=name)
            elif operation == "export":
                destination = export_terminology_package(source, name=name)
            else:
                raise CommandError("Unsupported package operation")
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(str(destination))
