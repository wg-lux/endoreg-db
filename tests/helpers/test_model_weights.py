from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from django.core.files.storage import default_storage

from endoreg_db.models import AiModel, LabelSet, ModelMeta
from tests.helpers.model_weights import (
    MANAGED_STUB_WEIGHT_PAYLOAD,
    ensure_managed_stub_weights,
)


@pytest.mark.django_db
def test_ensure_managed_stub_weights_recreates_missing_referenced_stub():
    suffix = f"missing-stub-{uuid4().hex}.safetensors"
    weights_name = f"model_weights/{suffix}"
    ai_model = AiModel.objects.create(name=f"stub-model-{uuid4().hex[:8]}")
    labelset = LabelSet.objects.create(
        name=f"stub-labelset-{uuid4().hex[:8]}",
        version=1,
    )
    meta = ModelMeta.objects.create(
        name=f"stub-meta-{uuid4().hex[:8]}",
        version="1",
        model=ai_model,
        labelset=labelset,
        weights=weights_name,
    )
    if default_storage.exists(weights_name):
        default_storage.delete(weights_name)

    ensure_managed_stub_weights(meta)

    weights_path = Path(default_storage.path(weights_name))
    assert weights_path.exists()
    assert weights_path.read_bytes() == MANAGED_STUB_WEIGHT_PAYLOAD
    meta.refresh_from_db()
    assert meta.weights.name == weights_name
