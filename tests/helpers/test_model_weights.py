from __future__ import annotations

from typing import BinaryIO, Protocol, cast
from uuid import uuid4

import pytest
from endoreg_db.models import AiModel, LabelSet, ModelMeta
from tests.helpers.model_weights import (
    MANAGED_STUB_WEIGHT_PAYLOAD,
    ensure_managed_stub_weights,
)


class _BinaryStorage(Protocol):
    def open(self, name: str, mode: str = "rb") -> BinaryIO: ...


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
    storage = meta.weights.storage
    if storage.exists(weights_name):
        storage.delete(weights_name)

    ensure_managed_stub_weights(meta)

    storage = cast(_BinaryStorage, meta.weights.storage)
    with storage.open(weights_name, "rb") as weights_file:
        assert weights_file.read() == MANAGED_STUB_WEIGHT_PAYLOAD
    is_encrypted = getattr(meta.weights.storage, "is_encrypted", None)
    if callable(is_encrypted):
        assert is_encrypted(weights_name) is True
    meta.refresh_from_db()
    assert meta.weights.name == weights_name
