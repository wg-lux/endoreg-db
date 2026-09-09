from __future__ import annotations

from typing import TypedDict, Unpack

from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)

from ...data import (
    AI_MODEL_DATA_DIR,
    AI_MODEL_META_DATA_DIR,  # Add this import
    MODEL_TYPE_DATA_DIR,
    VIDEO_SEGMENTATION_LABEL_DATA_DIR,
    VIDEO_SEGMENTATION_LABELSET_DATA_DIR,
)
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.administration.ai.model_type import ModelType
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.video_segmentation_label import VideoSegmentationLabel
from endoreg_db.models.label.video_segmentation_labelset import (
    VideoSegmentationLabelSet,
)
from endoreg_db.models.metadata.model_meta import ModelMeta
from ...utils import load_model_data_from_yaml
from ...utils.yaml_model_loader import LoadModelDataMetadata


class LoadAiModelCommandOptions(TypedDict):
    verbose: bool


IMPORT_MODELS: list[str] = [  # string as model key, serves as key in IMPORT_METADATA
    ModelType.__name__,
    VideoSegmentationLabel.__name__,
    VideoSegmentationLabelSet.__name__,
    AiModel.__name__,
    # ModelMeta.__name__,  # Disable automatic model meta loading
]

IMPORT_METADATA: dict[str, LoadModelDataMetadata] = {
    ModelType.__name__: {
        "dir": MODEL_TYPE_DATA_DIR,  # e.g. "intervention_types"
        "model": ModelType,
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
        "model": VideoSegmentationLabel,
        "foreign_keys": [],  # e.g. ["intervention_types"]
        "foreign_key_models": [],  # e.g. [InterventionType]
    },
    VideoSegmentationLabelSet.__name__: {
        "dir": VIDEO_SEGMENTATION_LABELSET_DATA_DIR,  # e.g. "interventions"
        "model": VideoSegmentationLabelSet,
        "foreign_keys": ["labels"],  # e.g. ["intervention_types"]
        "foreign_key_models": [VideoSegmentationLabel],  # e.g. [Intervention]
    },
    AiModel.__name__: {
        "dir": AI_MODEL_DATA_DIR,  # e.g. "intervention_types"
        "model": AiModel,
        "foreign_keys": [
            "video_segmentation_labelset",
            "model_type",
        ],  # e.g. ["interventions"]
        "foreign_key_models": [
            VideoSegmentationLabelSet,
            ModelType,
        ],  # e.g. [Intervention]
    },
}


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
        **options: Unpack[LoadAiModelCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose
        for model_name in IMPORT_MODELS:
            metadata = IMPORT_METADATA[model_name]
            load_model_data_from_yaml(self, model_name, metadata, verbose)
