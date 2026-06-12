# pyright: reportUnknownMemberType=false

from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch
from django.conf import LazySettings
from django.db import IntegrityError

from endoreg_db.models import AiModel, LabelSet, ModelMeta
from endoreg_db.services import model_meta_from_hf


def _fake_hf_download_factory(source_weights: Path) -> object:
    def fake_hf_download(**_kwargs: object) -> str:
        return source_weights.as_posix()

    return fake_hf_download


@pytest.mark.django_db
def test_ensure_model_meta_from_hf_repairs_existing_missing_weights(
    monkeypatch: MonkeyPatch, settings: LazySettings, tmp_path: Path
) -> None:
    settings.MEDIA_ROOT = tmp_path
    source_weights = tmp_path / "downloaded.safetensors"
    source_weights.write_bytes(b"downloaded weights")

    monkeypatch.setattr(
        model_meta_from_hf,
        "hf_hub_download",
        _fake_hf_download_factory(source_weights),
    )

    labelset, _ = LabelSet.objects.get_or_create(
        name="multilabel_classification_colonoscopy_default",
        version=1,
    )
    ai_model, _ = AiModel.objects.get_or_create(
        name="image_multilabel_classification_colonoscopy_default",
    )
    model_meta, _ = ModelMeta.objects.update_or_create(
        name="image_multilabel_classification_colonoscopy_default",
        model=ai_model,
        version="1",
        defaults={
            "labelset": labelset,
            "weights": "model_weights/missing.safetensors",
        },
    )
    ai_model.active_meta = None
    ai_model.save(update_fields=["active_meta"])

    result = model_meta_from_hf.ensure_model_meta_from_hf(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        model_name="image_multilabel_classification_colonoscopy_default",
        labelset_name=labelset.name,
        labelset_version=labelset.version,
        meta_version=model_meta.version,
    )

    result.refresh_from_db()
    ai_model.refresh_from_db()

    assert result.pk == model_meta.pk
    assert result.weights.name == "model_weights/missing.safetensors"
    assert Path(result.weights.path).read_bytes() == b"downloaded weights"
    assert ai_model.active_meta == result


@pytest.mark.django_db
def test_ensure_model_meta_from_hf_reuses_ai_model_after_unique_race(
    monkeypatch: MonkeyPatch, settings: LazySettings, tmp_path: Path
) -> None:
    settings.MEDIA_ROOT = tmp_path
    source_weights = tmp_path / "downloaded.safetensors"
    source_weights.write_bytes(b"downloaded weights")

    monkeypatch.setattr(
        model_meta_from_hf,
        "hf_hub_download",
        _fake_hf_download_factory(source_weights),
    )

    labelset, _ = LabelSet.objects.get_or_create(
        name="multilabel_classification_colonoscopy_default",
        version=1,
    )
    ai_model, _ = AiModel.objects.get_or_create(
        name="image_multilabel_classification_colonoscopy_default",
    )

    original_get_or_create = AiModel.objects.get_or_create
    state = {"raised": False}

    def raise_existing_name_once(*args: Any, **kwargs: Any) -> object:
        if not state["raised"] and kwargs.get("name") == ai_model.name:
            state["raised"] = True
            raise IntegrityError("UNIQUE constraint failed: endoreg_db_aimodel.name")
        return original_get_or_create(*args, **kwargs)

    monkeypatch.setattr(AiModel.objects, "get_or_create", raise_existing_name_once)

    result = model_meta_from_hf.ensure_model_meta_from_hf(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        model_name=ai_model.name,
        labelset_name=labelset.name,
        labelset_version=labelset.version,
        meta_version="1",
    )

    ai_model.refresh_from_db()

    assert state["raised"] is True
    assert result.model == ai_model
    assert ai_model.active_meta == result
