"""
Configuration loader for EndoReg DB setup.
Handles loading and parsing of setup configuration from YAML files.
"""

import glob
import logging
import os
from pathlib import Path

from pydantic import TypeAdapter

from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.utils.study_setup_yaml import parse_setup_yaml

from lx_dtypes.models.contracts.setup_config import (
    SetupConfigAutoGenerationDefaultsPayload,
    SetupConfigDataPayload,
    SetupConfigHuggingFaceFallbackPayload,
    SetupConfigModelSpecificEntryPayload,
    SetupConfigModelSpecificDataPayload,
)
from endoreg_db.utils.paths import get_runtime_paths

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
                Path(__file__).resolve().parents[1] / "data" / "setup_config.yaml"
            )

        self.config_file = config_file
        self._config = self._load_config()

    def _load_config(self) -> SetupConfigDataPayload:
        """Load configuration from YAML file."""
        config = SetupConfigDataPayload.model_validate(
            parse_setup_yaml(self.config_file.read_text(encoding="utf-8"))
        )
        logger.info("Loaded setup configuration from %s", self.config_file)
        return config

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
        resolved_dirs = [get_runtime_paths().weights]

        for dir_str in dirs:
            # Handle environment variable substitution
            if "${" in dir_str:
                dir_str = os.path.expandvars(dir_str)

            if "$" in dir_str:
                raise ValueError(
                    "Unresolved environment variable in weights_search_dirs"
                )
            path = Path(dir_str)
            if path not in resolved_dirs:
                resolved_dirs.append(path)

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
        from endoreg_db.data import AI_MODEL_META_DATA_DIR

        for yaml_file in sorted(AI_MODEL_META_DATA_DIR.glob("*.yaml")):
            entries = TypeAdapter(list[JsonObject]).validate_python(
                parse_setup_yaml(yaml_file.read_text(encoding="utf-8"))
            )
            for item in entries:
                metadata = TypeAdapter[JsonObject](JsonObject).validate_python(
                    item["fields"]
                )
                # These contracts own selectors and setup hints, not the ORM fixture.
                entry = SetupConfigModelSpecificEntryPayload.model_validate(
                    {
                        "fields": {
                            key: metadata[key]
                            for key in ("name", "model")
                            if key in metadata
                        }
                    }
                )
                if entry.fields.name == model_name or entry.fields.model == model_name:
                    return SetupConfigModelSpecificDataPayload.model_validate(
                        {"setup_config": item.get("setup_config", {})}
                    )
        return None


# Global instance for easy access
setup_config = SetupConfig()
