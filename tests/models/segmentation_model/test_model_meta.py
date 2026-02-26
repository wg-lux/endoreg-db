import pytest
from endoreg_db.models import ModelMeta
import endoreg_db.models.metadata.model_meta as model_meta_module


@pytest.mark.django_db
def test_model_meta_natural_key_and_manager_get_by_natural_key(
    unique_ai_model, base_labelset
):
    # Use unique_ai_model to ensure we have a clean slate
    meta, created = ModelMeta.objects.get_or_create(
        name="default_meta",
        version="1",
        model=unique_ai_model,
        labelset=base_labelset,
    )

    # Act
    natural_key = meta.natural_key()
    fetched = ModelMeta.objects.get_by_natural_key(*natural_key)

    # Assert
    assert natural_key == ("default_meta", "1", unique_ai_model.name)
    assert fetched.pk == meta.pk


@pytest.mark.django_db
def test_model_meta_str_representation(unique_ai_model, base_labelset):
    # Arrange: Use unique_ai_model to ensure no conflicts, and base_labelset for valid defaults
    meta = ModelMeta.objects.create(
        name="meta_name",
        version="2a",
        model=unique_ai_model,
        labelset=base_labelset,
        description="Some description",
    )

    s = str(meta)
    assert f"ModelMeta: meta_name (v2a) for {unique_ai_model.name}" == s


# ... (Rest of the file remains exactly the same as your previous version) ...


def test_get_latest_version_number_delegates_to_logic(monkeypatch):
    called = {}

    def fake_logic(cls, meta_name, model_name):
        called["cls"] = cls
        called["meta_name"] = meta_name
        called["model_name"] = model_name
        return 42

    monkeypatch.setattr(
        model_meta_module.logic,
        "get_latest_version_number_logic",
        fake_logic,
    )

    result = ModelMeta.get_latest_version_number("meta_x", "model_y")

    assert result == 42
    assert called["cls"] is ModelMeta
    assert called["meta_name"] == "meta_x"
    assert called["model_name"] == "model_y"


def test_create_from_file_delegates_to_logic(monkeypatch):
    called = {}

    def fake_logic(cls, meta_name, model_name, labelset_name, weights_file, **kwargs):
        called["cls"] = cls
        called["meta_name"] = meta_name
        called["model_name"] = model_name
        called["labelset_name"] = labelset_name
        called["weights_file"] = weights_file
        called["kwargs"] = kwargs
        return "SENTINEL_MODEL_META"

    monkeypatch.setattr(
        model_meta_module.logic,
        "create_from_file_logic",
        fake_logic,
    )

    result = ModelMeta.create_from_file(
        meta_name="my_meta",
        model_name="my_model",
        labelset_name="my_labelset",
        weights_file="weights.safetensors",
        labelset_version="3",
        requested_version="2a",
        bump_if_exists=True,
        extra_param="value",
    )

    assert result == "SENTINEL_MODEL_META"
    assert called["cls"] is ModelMeta
    assert called["meta_name"] == "my_meta"
    assert called["model_name"] == "my_model"
    assert called["labelset_name"] == "my_labelset"
    assert called["weights_file"] == "weights.safetensors"
    assert called["kwargs"]["labelset_version"] == "3"
    assert called["kwargs"]["requested_version"] == "2a"
    assert called["kwargs"]["bump_if_exists"] is True
    assert called["kwargs"]["extra_param"] == "value"


def test_setup_default_from_huggingface_delegates_to_logic(monkeypatch):
    called = {}

    def fake_logic(cls, model_id, labelset_name, labelset_version):
        called["cls"] = cls
        called["model_id"] = model_id
        called["labelset_name"] = labelset_name
        called["labelset_version"] = labelset_version
        return "SENTINEL_HF_META"

    monkeypatch.setattr(
        model_meta_module.logic,
        "setup_default_from_huggingface_logic",
        fake_logic,
    )

    result = ModelMeta.setup_default_from_huggingface(
        model_id="wg-lux/colo_segmentation_RegNetX800MF_base",
        labelset_name="image_multilabel_classification_colonoscopy_default",
        labelset_version=1,
    )

    assert result == "SENTINEL_HF_META"
    assert called["cls"] is ModelMeta
    assert called["model_id"] == "wg-lux/colo_segmentation_RegNetX800MF_base"
    assert (
        called["labelset_name"] == "image_multilabel_classification_colonoscopy_default"
    )
    assert called["labelset_version"] == 1


def test_get_activation_function_delegates_to_logic(monkeypatch):
    called = {}

    def fake_logic(activation_name):
        called["activation_name"] = activation_name
        return "SENTINEL_ACTIVATION"

    monkeypatch.setattr(
        model_meta_module.logic,
        "get_activation_function_logic",
        fake_logic,
    )

    result = ModelMeta.get_activation_function("sigmoid")

    assert result == "SENTINEL_ACTIVATION"
    assert called["activation_name"] == "sigmoid"


def test_get_inference_dataset_config_delegates_to_logic(monkeypatch):
    called = {}

    def fake_logic(instance):
        called["instance"] = instance
        return {"dummy": True}

    monkeypatch.setattr(
        model_meta_module.logic,
        "get_inference_dataset_config_logic",
        fake_logic,
    )

    # We don't need DB here, just any instance-like object; but using the real class is fine
    dummy = object()
    # Monkeypatch the method to accept any object and see it's passed through:
    result = ModelMeta.get_inference_dataset_config(dummy)

    assert result == {"dummy": True}
    assert called["instance"] is dummy


def test_get_config_dict_delegates_to_logic(monkeypatch):
    called = {}

    def fake_logic(instance):
        called["instance"] = instance
        return {"config": "value"}

    monkeypatch.setattr(
        model_meta_module.logic,
        "get_config_dict_logic",
        fake_logic,
    )

    dummy = object()
    result = ModelMeta.get_config_dict(dummy)

    assert result == {"config": "value"}
    assert called["instance"] is dummy


def test_get_by_name_version_delegates_to_logic(monkeypatch):
    called = {}

    def fake_logic(cls, meta_name, model_name, version):
        called["cls"] = cls
        called["meta_name"] = meta_name
        called["model_name"] = model_name
        called["version"] = version
        return "SENTINEL_META"

    monkeypatch.setattr(
        model_meta_module.logic,
        "get_model_meta_by_name_version_logic",
        fake_logic,
    )

    result = ModelMeta.get_by_name_version(
        meta_name="my_meta",
        model_name="my_model",
        version="2",
    )

    assert result == "SENTINEL_META"
    assert called["cls"] is ModelMeta
    assert called["meta_name"] == "my_meta"
    assert called["model_name"] == "my_model"
    assert called["version"] == "2"


def test_get_latest_delegates_to_logic_with_version_none(monkeypatch):
    called = {}

    def fake_logic(cls, meta_name, model_name, version):
        called["cls"] = cls
        called["meta_name"] = meta_name
        called["model_name"] = model_name
        called["version"] = version
        return "SENTINEL_LATEST_META"

    monkeypatch.setattr(
        model_meta_module.logic,
        "get_model_meta_by_name_version_logic",
        fake_logic,
    )

    result = ModelMeta.get_latest(
        meta_name="my_meta",
        model_name="my_model",
    )

    assert result == "SENTINEL_LATEST_META"
    assert called["cls"] is ModelMeta
    assert called["meta_name"] == "my_meta"
    assert called["model_name"] == "my_model"
    # get_latest must pass version=None
    assert called["version"] is None
