from __future__ import annotations


from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from endoreg_db.models.medical.contraindication import Contraindication

from ...data import CONTRAINDICATION_DATA_DIR as SOURCE_DIR
from ...utils import load_model_data_from_yaml
from endoreg_db.helpers.typing import LoadModelDataMetadata
from lx_dtypes.terminology.terminology_loader import (
    active_kb_identity,
    hydrate_shipped_terminology,
    load_module_kb,
)


MODEL_0 = Contraindication


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    MODEL_0.__name__,
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    MODEL_0.__name__: {
        "dir": SOURCE_DIR,  # e.g. "interventions"
        "model": MODEL_0,
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    }
}

registry_path = hydrate_shipped_terminology()

# Resolve a complete identity and keep it for the operation.
module_name, version = active_kb_identity()
kb = load_module_kb(module_name, version=version)

print(registry_path)
print(kb.config.source_file)


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: object,
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
