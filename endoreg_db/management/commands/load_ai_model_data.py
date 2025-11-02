from django.core.management.base import BaseCommand

from ...data import (
    AI_MODEL_DATA_DIR,
    AI_MODEL_META_DATA_DIR,  # Add this import
    MODEL_TYPE_DATA_DIR,
    VIDEO_SEGMENTATION_LABEL_DATA_DIR,
    VIDEO_SEGMENTATION_LABELSET_DATA_DIR,
)
from ...models import (
    AiModel,
    LabelSet,  # Add LabelSet import
    ModelMeta,  # Add ModelMeta back to imports
    ModelType,
    VideoSegmentationLabel,
    VideoSegmentationLabelSet,
)
from ...utils import load_model_data_from_yaml

IMPORT_MODELS = [  # string as model key, serves as key in IMPORT_METADATA
    ModelType.__name__,
    VideoSegmentationLabel.__name__,
    VideoSegmentationLabelSet.__name__,
    AiModel.__name__,
    ModelMeta.__name__,  # Re-enable ModelMeta loading
]

IMPORT_METADATA = {
    ModelType.__name__: {
        "dir": MODEL_TYPE_DATA_DIR,  # e.g. "intervention_types"
        "model": ModelType,  # e.g. InterventionType
        "foreign_keys": [],  # e.g. ["interventions"]
        "foreign_key_models": [],  # e.g. [Intervention]
    },
    ModelMeta.__name__: {
        "dir": AI_MODEL_META_DATA_DIR,  # e.g. "ai_model_meta"
        "model": ModelMeta,  # e.g. ModelMeta
        "foreign_keys": ["labelset", "model"],  # Foreign key relationships
        "foreign_key_models": [LabelSet, AiModel],  # Actual model classes
    },
    VideoSegmentationLabel.__name__: {
        "dir": VIDEO_SEGMENTATION_LABEL_DATA_DIR,  # e.g. "interventions"
        "model": VideoSegmentationLabel,  # e.g. Intervention
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    VideoSegmentationLabelSet.__name__: {
        "dir": VIDEO_SEGMENTATION_LABELSET_DATA_DIR,  # e.g. "interventions"
        "model": VideoSegmentationLabelSet,  # e.g. Intervention
        "foreign_keys": ["labels"],  # e.g. ["intervention_types"]
        "foreign_key_models": [VideoSegmentationLabel],  # e.g. [Intervention]
    },
    AiModel.__name__: {
        "dir": AI_MODEL_DATA_DIR,  # e.g. "intervention_types"
        "model": AiModel,  # e.g. InterventionType
        "foreign_keys": ["video_segmentation_labelset", "model_type"],  # e.g. ["interventions"]
        "foreign_key_models": [VideoSegmentationLabelSet, ModelType],  # e.g. [Intervention]
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
