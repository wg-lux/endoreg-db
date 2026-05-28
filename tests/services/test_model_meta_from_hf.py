from pathlib import Path

import pytest

from endoreg_db.models import AiModel, LabelSet, ModelMeta
from endoreg_db.services import model_meta_from_hf


@pytest.mark.django_db
def test_ensure_model_meta_from_hf_repairs_existing_missing_weights(
    monkeypatch,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    source_weights = tmp_path / "downloaded.safetensors"
    source_weights.write_bytes(b"downloaded weights")

    monkeypatch.setattr(
        model_meta_from_hf,
        "hf_hub_download",
        lambda **_kwargs: source_weights.as_posix(),
    )

    labelset = LabelSet.objects.create(
        name="multilabel_classification_colonoscopy_default",
        version=1,
    )
    ai_model = AiModel.objects.create(
        name="image_multilabel_classification_colonoscopy_default",
    )
    model_meta = ModelMeta.objects.create(
        name="image_multilabel_classification_colonoscopy_default",
        model=ai_model,
        version="1",
        labelset=labelset,
        weights="model_weights/missing.safetensors",
    )

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
