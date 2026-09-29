from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from lx_dtypes.terminology.terminology_service import TerminologyError

from endoreg_db.services.center_employees import import_center_employees


class Command(BaseCommand):
    help = (
        "Import optional employee lists from an explicitly versioned lx-dtypes package."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--module", required=True)
        parser.add_argument("--module-version", required=True)

    def handle(self, *args: str, **options: object) -> None:
        module = options["module"]
        version = options["module_version"]
        if not isinstance(module, str) or not isinstance(version, str):
            raise CommandError("An explicit module and version are required")
        try:
            knowledge_base = get_terminology_service().load(module, version)
            count = import_center_employees(knowledge_base)
        except (ValueError, OSError, TerminologyError):
            # Validation exceptions can contain staff names; keep CLI output private.
            raise CommandError(
                "Employee package import failed; verify package schema and center keys"
            ) from None
        self.stdout.write(self.style.SUCCESS(f"Imported {count} employee lists."))
