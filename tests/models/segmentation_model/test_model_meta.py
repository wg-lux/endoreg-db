from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Unpack, cast

import pytest
from pytest_django.fixtures import SettingsWrapper

from endoreg_db.models import AiModel, LabelSet, ModelMeta
from endoreg_db.models.metadata import model_meta_logic
from lx_dtypes.models.contracts.model_meta_logic import (
    ModelMetaCreateFromFileKwargsData,
    ModelMetaInferredDefaultsPayload,
)
from typing import Protocol


class _AiModelWithActiveMeta(Protocol):
    active_meta: ModelMeta | None


class _DownloadHfWeights(Protocol):
    def __call__(self, *, model_id: str) -> AbstractContextManager[Path]: ...


class _CreateFromFileDelegateKwargs(ModelMetaCreateFromFileKwargsData, total=False):
    labelset_version: str
    requested_version: str
    bump_if_exists: bool


def _empty_create_from_file_delegate_kwargs() -> _CreateFromFileDelegateKwargs:
    return {}


@dataclass
class _LatestVersionCall:
    cls: type[ModelMeta] = ModelMeta
    meta_name: str = ""
    model_name: str = ""


@dataclass
class _CreateFromFileCall:
    cls: type[ModelMeta] = ModelMeta
    meta_name: str = ""
    model_name: str = ""
    labelset_name: str = ""
    weights_file: str = ""
    kwargs: _CreateFromFileDelegateKwargs = field(
        default_factory=_empty_create_from_file_delegate_kwargs
    )


@dataclass
class _HuggingFaceSetupCall:
    cls: type[ModelMeta] = ModelMeta
    model_id: str = ""
    labelset_name: str = ""
    labelset_version: int = 0


@dataclass
class _ActivationCall:
    activation_name: str = ""


@dataclass
class _InstanceCall:
    instance: ModelMeta = field(default_factory=ModelMeta)


@dataclass
class _NameVersionCall:
    cls: type[ModelMeta] = ModelMeta
    meta_name: str = ""
    model_name: str = ""
    version: str = ""
    version_was_none: bool = False


@pytest.mark.django_db
def test_model_meta_natural_key_and_manager_get_by_natural_key(
    unique_ai_model: AiModel,
    base_labelset: LabelSet,
) -> None:
    meta, _created = ModelMeta.objects.get_or_create(
        name="default_meta",
        version="1",
        model=unique_ai_model,
        labelset=base_labelset,
    )

    natural_key = meta.natural_key()
    fetched = ModelMeta.get_by_natural_key(*natural_key)

    assert natural_key == ("default_meta", "1", unique_ai_model.name)
    assert fetched.pk == meta.pk


@pytest.mark.django_db
def test_model_meta_str_representation(
    unique_ai_model: AiModel,
    base_labelset: LabelSet,
) -> None:
    meta = ModelMeta.objects.create(
        name="meta_name",
        version="2a",
        model=unique_ai_model,
        labelset=base_labelset,
        description="Some description",
    )

    assert str(meta) == f"ModelMeta: meta_name (v2a) for {unique_ai_model.name}"


def test_get_latest_version_number_delegates_to_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _LatestVersionCall()

    def fake_logic(cls: type[ModelMeta], meta_name: str, model_name: str) -> int:
        called.cls = cls
        called.meta_name = meta_name
        called.model_name = model_name
        return 42

    monkeypatch.setattr(
        model_meta_logic,
        "get_latest_version_number_logic",
        fake_logic,
    )

    result = ModelMeta.get_latest_version_number("meta_x", "model_y")

    assert result == 42
    assert called.cls is ModelMeta
    assert called.meta_name == "meta_x"
    assert called.model_name == "model_y"


def test_create_from_file_delegates_to_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _CreateFromFileCall()

    def fake_logic(
        cls: type[ModelMeta],
        meta_name: str,
        model_name: str,
        labelset_name: str,
        weights_file: str,
        **kwargs: Unpack[_CreateFromFileDelegateKwargs],
    ) -> str:
        called.cls = cls
        called.meta_name = meta_name
        called.model_name = model_name
        called.labelset_name = labelset_name
        called.weights_file = weights_file
        called.kwargs = kwargs
        return "SENTINEL_MODEL_META"

    monkeypatch.setattr(
        model_meta_logic,
        "create_from_file_logic",
        fake_logic,
    )

    result = cast(
        str,
        ModelMeta.create_from_file(
            meta_name="my_meta",
            model_name="my_model",
            labelset_name="my_labelset",
            weights_file="weights.safetensors",
            labelset_version="3",
            requested_version="2a",
            bump_if_exists=True,
            activation="relu",
        ),
    )

    assert result == "SENTINEL_MODEL_META"
    assert called.cls is ModelMeta
    assert called.meta_name == "my_meta"
    assert called.model_name == "my_model"
    assert called.labelset_name == "my_labelset"
    assert called.weights_file == "weights.safetensors"
    assert called.kwargs.get("labelset_version") == "3"
    assert called.kwargs.get("requested_version") == "2a"
    assert called.kwargs.get("bump_if_exists") is True
    assert called.kwargs.get("activation") == "relu"


def test_setup_default_from_huggingface_delegates_to_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _HuggingFaceSetupCall()

    def fake_logic(
        cls: type[ModelMeta],
        model_id: str,
        labelset_name: str,
        labelset_version: int,
    ) -> str:
        called.cls = cls
        called.model_id = model_id
        called.labelset_name = labelset_name
        called.labelset_version = labelset_version
        return "SENTINEL_HF_META"

    monkeypatch.setattr(
        model_meta_logic,
        "setup_default_from_huggingface_logic",
        fake_logic,
    )

    result = cast(
        str,
        ModelMeta.setup_default_from_huggingface(
            model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
            labelset_name="image_multilabel_classification_colonoscopy_default",
            labelset_version=1,
        ),
    )

    assert result == "SENTINEL_HF_META"
    assert called.cls is ModelMeta
    assert called.model_id == "wg-lux/colo_segmentation_RegNetX800MF_base"
    assert called.labelset_name == "image_multilabel_classification_colonoscopy_default"
    assert called.labelset_version == 1


@pytest.mark.django_db
def test_setup_default_from_huggingface_repairs_existing_missing_weights(
    monkeypatch: pytest.MonkeyPatch,
    settings: SettingsWrapper,
    tmp_path: Path,
    unique_ai_model: AiModel,
    base_labelset: LabelSet,
) -> None:
    settings.MEDIA_ROOT = tmp_path
    download_staging_dirs: list[Path] = []

    model_meta = ModelMeta.objects.create(
        name=unique_ai_model.name,
        version="1",
        model=unique_ai_model,
        labelset=base_labelset,
        weights="model_weights/missing.safetensors",
    )

    def fake_infer_default_model_meta_from_hf(
        _model_id: str,
    ) -> ModelMetaInferredDefaultsPayload:
        return ModelMetaInferredDefaultsPayload(
            name=unique_ai_model.name,
            activation="sigmoid",
            mean=(0.1, 0.2, 0.3),
            std=(0.4, 0.5, 0.6),
            size_x=224,
            size_y=224,
            description="test hf model",
        )

    def fake_hf_hub_download(
        *,
        repo_id: str,
        filename: str,
        local_dir: str | Path,
    ) -> str:
        staging_dir = Path(local_dir)
        download_staging_dirs.append(staging_dir)
        source_weights = staging_dir / filename
        source_weights.write_bytes(b"downloaded weights")
        return source_weights.as_posix()

    monkeypatch.setattr(
        model_meta_logic,
        "infer_default_model_meta_from_hf",
        fake_infer_default_model_meta_from_hf,
    )
    monkeypatch.setattr(
        model_meta_logic,
        "hf_hub_download",
        fake_hf_hub_download,
    )

    result = ModelMeta.setup_default_from_huggingface(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        labelset_name=base_labelset.name,
        labelset_version=base_labelset.version,
    )

    result.refresh_from_db()
    unique_ai_model.refresh_from_db()

    assert result.pk == model_meta.pk
    assert result.weights.name == "model_weights/missing.safetensors"
    assert Path(result.weights.path).read_bytes() == b"downloaded weights"
    active_meta = cast(_AiModelWithActiveMeta, unique_ai_model).active_meta
    assert active_meta is not None
    assert active_meta == result
    assert len(download_staging_dirs) == 1
    assert not download_staging_dirs[0].exists()


def test_huggingface_download_rejects_artifact_outside_protected_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    weights_dir = tmp_path / "protected" / "model_weights"
    outside_artifact = tmp_path / "outside.safetensors"
    outside_artifact.write_bytes(b"untrusted-location")
    download_staging_dirs: list[Path] = []

    def fake_hf_hub_download(
        *,
        repo_id: str,
        filename: str,
        local_dir: str | Path,
    ) -> str:
        download_staging_dirs.append(Path(local_dir))
        return outside_artifact.as_posix()

    monkeypatch.setattr(model_meta_logic, "WEIGHTS_DIR", weights_dir)
    monkeypatch.setattr(
        model_meta_logic,
        "hf_hub_download",
        fake_hf_hub_download,
    )
    download_hf_weights = cast(
        _DownloadHfWeights,
        getattr(model_meta_logic, "_download_hf_weights"),
    )

    with pytest.raises(RuntimeError, match="outside the protected"):
        with download_hf_weights(model_id="wg-lux/test-model"):
            pytest.fail("An out-of-bound artifact must never be yielded")

    assert outside_artifact.read_bytes() == b"untrusted-location"
    assert len(download_staging_dirs) == 1
    assert not download_staging_dirs[0].exists()


def test_get_activation_function_delegates_to_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _ActivationCall()

    def fake_logic(activation_name: str) -> str:
        called.activation_name = activation_name
        return "SENTINEL_ACTIVATION"

    monkeypatch.setattr(
        model_meta_logic,
        "get_activation_function_logic",
        fake_logic,
    )

    result = cast(str, ModelMeta.get_activation_function("sigmoid"))

    assert result == "SENTINEL_ACTIVATION"
    assert called.activation_name == "sigmoid"


def test_get_inference_dataset_config_delegates_to_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _InstanceCall()

    def fake_logic(instance: ModelMeta) -> dict[str, bool]:
        called.instance = instance
        return {"dummy": True}

    monkeypatch.setattr(
        model_meta_logic,
        "get_inference_dataset_config_logic",
        fake_logic,
    )

    dummy = ModelMeta(name="dummy", version="1")
    result = cast(dict[str, bool], dummy.get_inference_dataset_config())

    assert result == {"dummy": True}
    assert called.instance is dummy


def test_get_config_dict_delegates_to_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _InstanceCall()

    def fake_logic(instance: ModelMeta) -> dict[str, str]:
        called.instance = instance
        return {"config": "value"}

    monkeypatch.setattr(
        model_meta_logic,
        "get_config_dict_logic",
        fake_logic,
    )

    dummy = ModelMeta(name="dummy", version="1")
    result = cast(dict[str, str], dummy.get_config_dict())

    assert result == {"config": "value"}
    assert called.instance is dummy


def test_get_by_name_version_delegates_to_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _NameVersionCall()

    def fake_logic(
        cls: type[ModelMeta],
        meta_name: str,
        model_name: str,
        version: str,
    ) -> str:
        called.cls = cls
        called.meta_name = meta_name
        called.model_name = model_name
        called.version = version
        return "SENTINEL_META"

    monkeypatch.setattr(
        model_meta_logic,
        "get_model_meta_by_name_version_logic",
        fake_logic,
    )

    result = cast(
        str,
        ModelMeta.get_by_name_version(
            meta_name="my_meta",
            model_name="my_model",
            version="2",
        ),
    )

    assert result == "SENTINEL_META"
    assert called.cls is ModelMeta
    assert called.meta_name == "my_meta"
    assert called.model_name == "my_model"
    assert called.version == "2"


def test_get_latest_delegates_to_logic_with_version_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = _NameVersionCall(version="not-called")

    def fake_logic(
        cls: type[ModelMeta],
        meta_name: str,
        model_name: str,
        version: str | None,
    ) -> str:
        called.cls = cls
        called.meta_name = meta_name
        called.model_name = model_name
        called.version_was_none = version is None
        called.version = "" if version is None else version
        return "SENTINEL_LATEST_META"

    monkeypatch.setattr(
        model_meta_logic,
        "get_model_meta_by_name_version_logic",
        fake_logic,
    )

    result = cast(
        str,
        ModelMeta.get_latest(
            meta_name="my_meta",
            model_name="my_model",
        ),
    )

    assert result == "SENTINEL_LATEST_META"
    assert called.cls is ModelMeta
    assert called.meta_name == "my_meta"
    assert called.model_name == "my_model"
    assert called.version_was_none is True
