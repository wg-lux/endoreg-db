# pyright: reportPrivateUsage=false
import os
from pathlib import Path
from typing import Protocol, cast

import pytest
from django.conf import settings
from lx_dtypes.models.contracts.model_meta_logic import (
    ModelMetaCreateFromFileKwargsData,
    ModelMetaCreateFromFilePayload,
)
from endoreg_db.utils.ffmpeg_wrapper import is_ffmpeg_available

from endoreg_db.models import ModelMeta
from endoreg_db.models.metadata import VideoPredictionMeta
from endoreg_db.services.video_files._ai import _is_stub_weights_file
from tests.helpers.data_loader import load_ai_model_data, load_ai_model_label_data
from tests.helpers.default_objects import get_default_video_file

SKIP_EXPENSIVE_TESTS = os.environ.get("SKIP_EXPENSIVE_TESTS", "true").lower() == "true"
FFMPEG_AVAILABLE = is_ffmpeg_available()


def _skip_unless_video_tests_enabled() -> None:
    if SKIP_EXPENSIVE_TESTS:
        pytest.skip("Skipping expensive inference test (SKIP_EXPENSIVE_TESTS=true)")
    if not settings.RUN_VIDEO_TESTS:
        pytest.skip("Video tests disabled (RUN_VIDEO_TESTS=false)")
    if not FFMPEG_AVAILABLE:
        pytest.skip("FFmpeg command not available")


def _prepare_video_file():
    return get_default_video_file()


@pytest.mark.expensive
@pytest.mark.video
@pytest.mark.ai
@pytest.mark.django_db(transaction=True)
def test_predict_video_with_huggingface_weights(base_db_data: object) -> None:
    _skip_unless_video_tests_enabled()

    load_ai_model_label_data()
    load_ai_model_data()

    model_meta = ModelMeta.setup_default_from_huggingface(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        labelset_name="multilabel_classification_colonoscopy_default",
        labelset_version=1,
    )

    weights_path = Path(model_meta.weights.path)
    assert weights_path.exists(), "Downloaded Hugging Face weights must exist"
    assert not _is_stub_weights_file(weights_path), (
        "Hugging Face weights should not be treated as stubs"
    )

    video_file = _prepare_video_file()
    model_meta_like = cast(_ModelMetaLike, model_meta)
    ai_model: _AiModelLike | None = None
    try:
        sequences = video_file.predict_video(
            model_meta=model_meta,
            test_run=True,
            n_test_frames=16,
            frame_source_mode="stream",
        )

        assert isinstance(sequences, dict)
        assert sequences, "Inference with Hugging Face weights should yield predictions"
        assert all(isinstance(spans, list) for spans in sequences.values())
        labelset = cast(_LabelSetLike, model_meta_like.labelset)
        label_names = {label.name for label in labelset.get_labels_in_order()}
        assert set(sequences.keys()).issubset(label_names)

        prediction_meta_exists = VideoPredictionMeta.objects.filter(
            video_file=video_file,
            model_meta=model_meta,
        ).exists()
        assert prediction_meta_exists, (
            "VideoPredictionMeta should be created for the inference run"
        )

    finally:
        ai_model = model_meta_like.model
        weights = model_meta.weights
        video_file.delete_with_file()
        if weights.name:
            weights.delete(save=False)
        model_meta.delete()
        if ai_model and not ai_model.metadata_versions.exists():
            ai_model.delete()


@pytest.mark.expensive
@pytest.mark.video
@pytest.mark.ai
@pytest.mark.django_db(transaction=True)
def test_predict_video_with_local_fixture_weights(base_db_data: object) -> None:
    _skip_unless_video_tests_enabled()

    load_ai_model_label_data()
    load_ai_model_data()

    fixture_path = Path(
        "tests/assets/colo_segmentation_RegNetX800MF_6.safetensors"
    ).resolve()
    assert fixture_path.exists(), "Local safetensors fixture must exist"

    meta_name = "test_local_colonoscopy_inference"
    ModelMeta.objects.filter(name=meta_name).delete()

    create_kwargs = cast(
        ModelMetaCreateFromFileKwargsData,
        ModelMetaCreateFromFilePayload(
            activation="sigmoid",
            mean="0.45211223,0.27139644,0.19264949",
            std="0.31418097,0.21088019,0.16059452",
            size_x=716,
            size_y=716,
            axes="2,0,1",
            batchsize=4,
            num_workers=0,
            description="Local fixture weights for inference integration test",
        ).model_dump(mode="python"),
    )
    model_meta = ModelMeta.create_from_file(
        meta_name=meta_name,
        model_name="image_multilabel_classification_colonoscopy_default",
        labelset_name="multilabel_classification_colonoscopy_default",
        labelset_version=1,
        weights_file=str(fixture_path),
        requested_version="1",
        bump_if_exists=True,
        **create_kwargs,
    )

    weights_path = Path(model_meta.weights.path)
    assert weights_path.exists(), "Copied local weights must be present"
    assert not _is_stub_weights_file(weights_path), (
        "Local fixture weights should not be treated as stubs"
    )

    video_file = _prepare_video_file()
    try:
        sequences = video_file.predict_video(
            model_meta=model_meta,
            test_run=True,
            n_test_frames=16,
            frame_source_mode="stream",
        )

        assert isinstance(sequences, dict)
        assert sequences, (
            "Inference with local fixture weights should yield predictions"
        )
        assert all(isinstance(spans, list) for spans in sequences.values())
        model_meta_like = cast(_ModelMetaLike, model_meta)
        labelset = cast(_LabelSetLike, model_meta_like.labelset)
        label_names = {label.name for label in labelset.get_labels_in_order()}
        assert set(sequences.keys()).issubset(label_names)

        prediction_meta_exists = VideoPredictionMeta.objects.filter(
            video_file=video_file,
            model_meta=model_meta,
        ).exists()
        assert prediction_meta_exists, (
            "VideoPredictionMeta should be created for the inference run"
        )

    finally:
        weights = model_meta.weights
        video_file.delete_with_file()
        if weights.name:
            weights.delete(save=False)
        model_meta.delete()


class _MetadataVersionsLike(Protocol):
    def exists(self) -> bool: ...
    def delete(self) -> None: ...


class _AiModelLike(Protocol):
    metadata_versions: _MetadataVersionsLike

    def delete(self) -> None: ...


class _LabelLike(Protocol):
    name: str


class _LabelSetLike(Protocol):
    def get_labels_in_order(self) -> list[_LabelLike]: ...


class _ModelMetaLike(Protocol):
    labelset: _LabelSetLike | None
    model: _AiModelLike | None
