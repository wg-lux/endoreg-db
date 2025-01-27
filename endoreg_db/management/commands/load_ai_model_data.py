from django.core.management.base import BaseCommand

from ...data import AI_MODEL_DATA_DIR, VIDEO_SEGMENTATION_LABEL_DATA_DIR
from ...models import MultilabelVideoSegmentationModel, VideoSegmentationLabel
from ...utils import load_model_data_from_yaml

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    VideoSegmentationLabel.__name__,
    MultilabelVideoSegmentationModel.__name__,
]

IMPORT_METADATA = {
    VideoSegmentationLabel.__name__: {
        "dir": VIDEO_SEGMENTATION_LABEL_DATA_DIR,  # e.g. "interventions"
        "model": VideoSegmentationLabel,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    MultilabelVideoSegmentationModel.__name__: {
        "dir": AI_MODEL_DATA_DIR,  # e.g. "intervention_types"
        "model": MultilabelVideoSegmentationModel,  # e.g. InterventionType
        "foreign_keys": ["labels"],  # e.g. ["interventions"]
        "foreign_key_models": [VideoSegmentationLabel],  # e.g. [Intervention]
    },
}


class Command(BaseCommand):
    help = """Load all .yaml files in the data/intervention directory
    into the Intervention and InterventionType model"""

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(self, *args, **options):
        verbose = options["verbose"]
        for model_name in IMPORT_MODELS:
            _metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, _metadata, verbose)
