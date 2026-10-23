from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.management import call_command

from endoreg_db.models import AIDataSet, LabelSet


@pytest.mark.django_db
def test_model_input_command_forwards_annotation_source_scope_to_builder():
    dataset = AIDataSet.objects.create(
        name=f"model-input-scope-{uuid4().hex[:8]}",
        dataset_type=AIDataSet.DATASET_TYPE_IMAGE,
        ai_model_type=AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL,
    )
    labelset = LabelSet.objects.create(
        name=f"model-input-labelset-{uuid4().hex[:8]}",
        version=1,
    )

    with (
        patch(
            "endoreg_db.management.commands.model_input.build_dataset_for_training"
        ) as mocked_builder,
        patch("builtins.input", return_value="no"),
    ):
        mocked_builder.return_value = {
            "image_paths": [],
            "label_vectors": [],
            "label_masks": [],
            "labels": [],
            "labelset": labelset,
        }

        call_command(
            "model_input",
            dataset_id=dataset.pk,
            annotation_source_scope="segment_only",
        )

    assert mocked_builder.call_args.args[0] == dataset
    assert mocked_builder.call_args.kwargs["annotation_source_scope"] == "segment_only"
