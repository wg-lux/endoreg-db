from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from django.core.management.base import BaseCommand, CommandError, CommandParser
from lx_dtypes.terminology.terminology_loader import get_terminology_service
from lx_dtypes.terminology.terminology_service import TerminologyError

from endoreg_db.services.study_presets import import_study_preset


class Command(BaseCommand):
    help = "Import a versioned lx-dtypes preset from the shared terminology registry."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--module", required=True)
        parser.add_argument("--module-version", required=True)

    def handle(self, *args: str, **options: object) -> None:
        module = options["module"]
        version = options["module_version"]
        if not isinstance(module, str) or not isinstance(version, str):
            raise CommandError("An explicit module and module version are required")
        try:
            package = get_terminology_service().load(module, version)
            import_study_preset(package)
        except (
            ValueError,
            OSError,
            TerminologyError,
            ObjectDoesNotExist,
            MultipleObjectsReturned,
        ):
            raise CommandError(
                "Preset import failed; verify the registered package, unique identities and clinical dependencies"
            ) from None
        self.stdout.write(self.style.SUCCESS("Study preset imported."))
