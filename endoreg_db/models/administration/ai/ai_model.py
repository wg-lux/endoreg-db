"""
Django model for AI models.
"""

from __future__ import annotations

from logging import getLogger
from pathlib import Path
from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast, Any

from django.db import models
from icecream import ic

logger = getLogger(__name__)

DEFAULT_HF_MODEL_ID = "wg-lux/colo_segmentation_RegNetX800MF_base"
DEFAULT_PREDICTION_MODEL_NAME = "image_multilabel_classification_colonoscopy_default"
DEFAULT_PREDICTION_LABELSET_NAME = "multilabel_classification_colonoscopy_default"

NoAiModelRelationValue: TypeAlias = NoneType

if TYPE_CHECKING:
    from ...metadata.model_meta import ModelMeta


class _AiModelPkSource(Protocol):
    pk: int


class _AiModelMetaModelSource(Protocol):
    model: _AiModelPkSource


class _AiModelLabelSetSource(Protocol):
    name: str
    version: int


class _AiModelVersionSource(Protocol):
    version: str


class AiModelManager(models.Manager["AiModel"]):
    """
    Manager for AI models with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "AiModel":
        """
        Retrieves the AiModel instance with the specified unique name.

        Args:
            name: The unique identifier of the AiModel to retrieve.

        Returns:
            The AiModel instance matching the given name.

        Raises:
            AiModel.DoesNotExist: If no AiModel with the specified name exists.
        """
        return self.get(name=name)


class AiModel(models.Model):
    """
    Represents a generic AI model that encapsulates high-level metadata about the model,
    including names (default, German, and English), a description, categorization details,
    and associated label sets and meta information.

        name (str): Unique name of the AI model.
        description (str): Detailed description of the AI model when configured.
        model_type (str): Type/category of the AI model when configured.
        model_subtype (str): Subtype within the broader model type when configured.
        video_segmentation_labelset (VideoSegmentationLabelSet): associated label set for video segmentation tasks when configured.
        active_meta (ModelMeta): reference to the currently active ModelMeta instance associated with the model when configured.
    """

    objects = AiModelManager()

    name: models.CharField[Any, Any] = models.CharField(max_length=255, unique=True)

    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    model_type: models.ForeignKey[Any, Any] = models.ForeignKey(
        "ModelType",
        on_delete=models.CASCADE,
        related_name="ai_models",
        blank=True,
        null=True,
    )
    model_subtype: models.CharField[Any, Any] = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    video_segmentation_labelset: models.ForeignKey[Any, Any] = models.ForeignKey(
        "VideoSegmentationLabelSet",
        on_delete=models.CASCADE,
        related_name="ai_models",
        blank=True,
        null=True,
    )
    active_meta: models.ForeignKey[Any, Any] = models.ForeignKey(
        "ModelMeta",
        on_delete=models.SET_NULL,
        related_name="active_model",
        blank=True,
        null=True,
    )

    if TYPE_CHECKING:
        metadata_versions: models.QuerySet[ModelMeta]

    def get_version(self, version: int) -> ModelMeta:
        """
        Retrieves the ModelMeta instance for the specified version.

        If the active_meta matches the requested version, it is returned. Otherwise, searches related metadata_versions for a matching version. Raises ValueError if no matching metadata is found.

        Args:
            version: The version number of the desired ModelMeta.

        Returns:
            The ModelMeta instance corresponding to the specified version.

        Raises:
            ValueError: If no ModelMeta with the given version exists.
        """
        self._ensure_saved()
        requested_version = str(version)

        active_meta = self.active_meta
        if active_meta is not None:
            active_version = cast(_AiModelVersionSource, active_meta)
            if active_version.version == requested_version:
                return self._ensure_model_meta_ready(active_meta, "Active")

        # Get the model metadata with the given version
        model_meta = self.metadata_versions.filter(version=requested_version).first()
        if model_meta is not None:
            return self._ensure_model_meta_ready(model_meta, "Requested")

        raise ValueError(f"No model metadata found for version {requested_version}.")

    def _ensure_saved(self) -> None:
        if self.pk is None:
            raise ValueError("Cannot resolve model metadata for an unsaved AiModel.")

    @staticmethod
    def _model_meta_weights_exist(model_meta: ModelMeta) -> bool:
        if not model_meta.weights:
            return False
        try:
            return Path(model_meta.weights.path).exists()
        except (OSError, ValueError):
            return False

    def _ensure_default_huggingface_weights(self, model_meta: ModelMeta) -> ModelMeta:
        if self.name != DEFAULT_PREDICTION_MODEL_NAME:
            raise ValueError(
                f"Model weights for '{self.name}' are missing and no Hugging Face fallback is configured."
            )

        from endoreg_db.services.model_meta_from_hf import ensure_model_meta_from_hf

        labelset = cast(_AiModelLabelSetSource, getattr(model_meta, "labelset"))
        versioned_meta = cast(_AiModelVersionSource, model_meta)
        return ensure_model_meta_from_hf(
            model_id=DEFAULT_HF_MODEL_ID,
            model_name=self.name,
            labelset_name=labelset.name,
            meta_version=str(versioned_meta.version),
            labelset_version=labelset.version,
        )

    def _ensure_model_meta_belongs_to_self(self, model_meta: ModelMeta) -> None:
        model = cast(_AiModelMetaModelSource, model_meta)
        model_pk = model.model.pk
        if model_pk != self.pk:
            raise ValueError(
                f"ModelMeta {model_meta.pk} belongs to AiModel {model_pk}, "
                f"not AiModel {self.pk}."
            )

    def _repair_missing_default_weights(
        self, model_meta: ModelMeta, source: str
    ) -> ModelMeta:
        if self.name != DEFAULT_PREDICTION_MODEL_NAME:
            raise ValueError(
                f"{source} ModelMeta {model_meta.pk} for AiModel '{self.name}' has "
                "no available weights file and no Hugging Face fallback is configured."
            )

        logger.warning(
            "%s ModelMeta %s for AiModel '%s' has no available weights file; "
            "attempting Hugging Face repair.",
            source,
            model_meta.pk,
            self.name,
        )
        repaired_meta = self._ensure_default_huggingface_weights(model_meta)
        self._ensure_model_meta_belongs_to_self(repaired_meta)
        if not self._model_meta_weights_exist(repaired_meta):
            raise ValueError(
                f"Hugging Face repair for AiModel '{self.name}' returned ModelMeta "
                f"{repaired_meta.pk} without an available weights file."
            )
        return repaired_meta

    def _ensure_model_meta_ready(self, model_meta: ModelMeta, source: str) -> ModelMeta:
        self._ensure_model_meta_belongs_to_self(model_meta)
        if self._model_meta_weights_exist(model_meta):
            return model_meta
        return self._repair_missing_default_weights(model_meta, source)

    def get_latest_version(self) -> ModelMeta:
        self._ensure_saved()

        active_meta = self.active_meta
        if active_meta is not None:
            return self._ensure_model_meta_ready(active_meta, "Active")

        latest_version = self.metadata_versions.order_by("-version").first()
        if latest_version is not None:
            return self._ensure_model_meta_ready(latest_version, "Latest")

        if self.name != DEFAULT_PREDICTION_MODEL_NAME:
            raise ValueError(
                f"No model metadata found for AiModel '{self.name}' and no "
                "Hugging Face fallback is configured."
            )

        logger.info(
            "No local default segmentation model metadata was available; "
            "attempting Hugging Face setup for %s.",
            DEFAULT_HF_MODEL_ID,
        )
        from endoreg_db.services.model_meta_from_hf import ensure_model_meta_from_hf

        model_meta = ensure_model_meta_from_hf(
            model_id=DEFAULT_HF_MODEL_ID,
            model_name=DEFAULT_PREDICTION_MODEL_NAME,
            labelset_name=DEFAULT_PREDICTION_LABELSET_NAME,
            meta_version="1",
        )
        return self._ensure_model_meta_ready(model_meta, "Default Hugging Face")

    @classmethod
    def set_active_model_meta(
        cls, model_name: str, meta_name: str, meta_version: int
    ) -> None:
        """
        Sets the active metadata version for the specified AI model.

        Updates the `active_meta` field of the AiModel identified by `model_name` to the ModelMeta instance matching `meta_name` and `meta_version`.
        """
        from ...metadata.model_meta import ModelMeta

        model = cls.objects.get(name=model_name)

        ic(f"Getting model meta for {model_name} {meta_name} {meta_version}")

        model_meta = ModelMeta.objects.get(
            name=meta_name, model=model, version=str(meta_version)
        )

        model.active_meta = model_meta
        model.save(update_fields=["active_meta"])

        ic(
            f"Set active model meta for {model_name} to {meta_name} version {meta_version}"
        )

    def natural_key(self) -> tuple[str]:
        """
        Return the natural key for this model.
        """
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
