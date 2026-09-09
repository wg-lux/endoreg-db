"""
Configuration loader for EndoReg DB setup.
Handles loading and parsing of setup configuration from YAML files.
"""

import glob
import logging
import os
from pathlib import Path
from typing import cast

import yaml

from lx_dtypes.models.contracts.setup_config import (
    SetupConfigAutoGenerationDefaultsPayload,
    SetupConfigDataPayload,
    SetupConfigDefaultModelsPayload,
    SetupConfigHuggingFaceFallbackPayload,
    SetupConfigModelSpecificEntryPayload,
    SetupConfigModelSpecificDataPayload,
)

logger = logging.getLogger(__name__)


class SetupConfig:
    """
    Handles loading and accessing setup configuration from YAML files.
    Provides methods to get model names, search patterns, and fallback configurations.
    """

    def __init__(self, config_file: Path | None = None):
        """
        Initialize the setup configuration.

        Args:
            config_file: Path to the setup configuration YAML file.
                        If None, uses default location.
        """
        if config_file is None:
            # Default to setup_config.yaml in data directory
            config_file = (
                Path(__file__).resolve().parents[2] / "data" / "setup_config.yaml"
            )

        self.config_file = config_file
        self._config = self._load_config()

    def _load_config(self) -> SetupConfigDataPayload:
        """Load configuration from YAML file."""
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = cast(dict[str, object], yaml.safe_load(f) or {})
                    logger.info(f"Loaded setup configuration from {self.config_file}")
                    return SetupConfigDataPayload.model_validate(config)
            else:
                logger.warning(f"Setup config file not found: {self.config_file}")
                return self._get_default_config()
        except Exception as e:
            logger.error(f"Error loading setup config: {e}")
            return self._get_default_config()

    def _get_default_config(self) -> SetupConfigDataPayload:
        """Return default configuration if file is not available."""
        return SetupConfigDataPayload(
            default_models=SetupConfigDefaultModelsPayload(
                primary_classification_model="image_multilabel_classification_colonoscopy_default",
                primary_labelset="multilabel_classification_colonoscopy_default",
            ),
            huggingface_fallback=SetupConfigHuggingFaceFallbackPayload(
                enabled=True,
                repo_id="wg-lux/colo_segmentation_RegNetX800MF_base",
                filename="colo_segmentation_RegNetX800MF_base.safetensors",
                labelset_name="multilabel_classification_colonoscopy_default",
            ),
            weights_search_patterns=[
                "colo_segmentation_RegNetX800MF_*.safetensors",
                "image_multilabel_classification_colonoscopy_default_*.safetensors",
                "*_colonoscopy_*.safetensors",
            ],
            weights_search_dirs=[
                "tests/assets",
                "assets",
                "model_weights",
                "${STORAGE_DIR}/model_weights",
            ],
            auto_generation_defaults=SetupConfigAutoGenerationDefaultsPayload(
                activation="sigmoid",
                mean="0.485,0.456,0.406",
                std="0.229,0.224,0.225",
                size_x=224,
                size_y=224,
                axes="CHW",
                batchsize=32,
                num_workers=4,
            ),
        )

    def get_primary_model_name(self) -> str:
        """Get the primary classification model name."""
        return self._config.default_models.primary_classification_model

    def get_primary_labelset_name(self) -> str:
        """Get the primary labelset name."""
        return self._config.default_models.primary_labelset

    def get_huggingface_config(self) -> SetupConfigHuggingFaceFallbackPayload:
        """Get HuggingFace fallback configuration."""
        return self._config.huggingface_fallback

    def get_weights_search_patterns(self) -> list[str]:
        """Get weight file search patterns."""
        return self._config.weights_search_patterns

    def get_weights_search_dirs(self) -> list[Path]:
        """
        Get weight file search directories with environment variable substitution.
        """
        dirs = self._config.weights_search_dirs
        resolved_dirs: list[Path] = []

        for dir_str in dirs:
            # Handle environment variable substitution
            if "${" in dir_str:
                dir_str = os.path.expandvars(dir_str)

            resolved_dirs.append(Path(dir_str))

        return resolved_dirs

    def get_auto_generation_defaults(self) -> SetupConfigAutoGenerationDefaultsPayload:
        """Get default values for auto-generated metadata."""
        return self._config.auto_generation_defaults

    def find_model_weights_files(self) -> list[Path]:
        """
        Find model weight files using configured search patterns and directories.

        Returns:
            List of paths to found weight files
        """
        found_files: list[Path] = []
        search_dirs = self.get_weights_search_dirs()
        search_patterns = self.get_weights_search_patterns()

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue

            for pattern in search_patterns:
                # Use glob to find files matching pattern
                pattern_path = search_dir / pattern
                matches = glob.glob(str(pattern_path))
                for match in matches:
                    path = Path(match)
                    if path.exists() and path not in found_files:
                        found_files.append(path)
                        logger.info(f"Found weight file: {path}")

        return found_files

    def get_model_specific_config(
        self, model_name: str
    ) -> SetupConfigModelSpecificDataPayload | None:
        """
        Get model-specific configuration from YAML metadata files.

        Args:
            model_name: Name of the model to get config for

        Returns:
            Model-specific setup configuration if found
        """
        # This would need to parse the ai_model_meta YAML files
        # and extract setup_config sections for the specified model
        try:
            from endoreg_db.data import AI_MODEL_META_DATA_DIR

            for yaml_file in AI_MODEL_META_DATA_DIR.glob("*.yaml"):
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = cast(list[object], yaml.safe_load(f) or [])

                for item in data:
                    typed_item = SetupConfigModelSpecificEntryPayload.model_validate(
                        item
                    )
                    fields = typed_item.fields
                    if fields.name == model_name or fields.model == model_name:
                        return SetupConfigModelSpecificDataPayload.model_validate(
                            typed_item.model_dump(mode="python")
                        )
        except Exception as e:
            logger.warning(f"Error loading model-specific config for {model_name}: {e}")

        return None


# Global instance for easy access
setup_config = SetupConfig()
