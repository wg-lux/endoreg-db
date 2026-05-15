from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

from django.test import override_settings


CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "endoreg_db"
    / "utils"
    / "ai"
    / "model_training"
    / "config.py"
)


def load_model_training_config() -> ModuleType:
    module_name = "_model_training_config_under_test"
    spec = importlib.util.spec_from_file_location(module_name, CONFIG_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_model_training_paths_use_runtime_data_dir_without_import_mkdir(
    monkeypatch,
    tmp_path,
) -> None:
    data_root = tmp_path / "runtime-data"
    fake_base_dir = tmp_path / "site-packages"

    monkeypatch.setenv("DATA_DIR", str(data_root))

    with override_settings(BASE_DIR=fake_base_dir):
        config = load_model_training_config()

    assert config.BASE_DIR == fake_base_dir
    assert config.TRAINING_ROOT == data_root.resolve() / "model_training"
    assert config.CHECKPOINTS_DIR == config.TRAINING_ROOT / "checkpoints"
    assert config.RUNS_DIR == config.TRAINING_ROOT / "runs"
    assert not (fake_base_dir / "data").exists()
    assert not config.TRAINING_ROOT.exists()

    config.ensure_training_directories()

    assert config.TRAINING_ROOT.is_dir()
    assert config.CHECKPOINTS_DIR.is_dir()
    assert config.RUNS_DIR.is_dir()


def test_model_training_config_regular_import_keeps_ai_exports_lazy(
    monkeypatch,
    tmp_path,
) -> None:
    data_root = tmp_path / "runtime-data"
    monkeypatch.setenv("DATA_DIR", str(data_root))

    ai_package = importlib.import_module("endoreg_db.utils.ai")
    for export_name in ai_package.__all__:
        ai_package.__dict__.pop(export_name, None)
    sys.modules.pop("endoreg_db.utils.ai.model_training.config", None)

    config = importlib.import_module("endoreg_db.utils.ai.model_training.config")

    assert config.TRAINING_ROOT == data_root.resolve() / "model_training"
    assert "InferenceDataset" not in ai_package.__dict__
    assert "MultiLabelClassificationNet" not in ai_package.__dict__
    assert "Classifier" not in ai_package.__dict__
