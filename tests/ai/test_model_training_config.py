from __future__ import annotations

from importlib import import_module
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import sys
from pathlib import Path
from typing import Protocol, cast

from django.test import override_settings
import pytest


CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "endoreg_db"
    / "utils"
    / "ai"
    / "model_training"
    / "config.py"
)


class ModelTrainingConfigModule(Protocol):
    BASE_DIR: Path
    TRAINING_ROOT: Path
    CHECKPOINTS_DIR: Path
    RUNS_DIR: Path

    def ensure_training_directories(self) -> None: ...


def load_model_training_config() -> ModelTrainingConfigModule:
    module_name = "_model_training_config_under_test"
    loader = SourceFileLoader(module_name, str(CONFIG_PATH))
    spec = spec_from_loader(module_name, loader)
    assert spec is not None

    module = module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return cast(ModelTrainingConfigModule, module)


def test_model_training_paths_use_runtime_data_dir_without_import_mkdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "runtime-data"
    monkeypatch.setenv("DATA_DIR", str(data_root))

    ai_package = import_module("endoreg_db.utils.ai")
    for export_name in ai_package.__all__:
        ai_package.__dict__.pop(export_name, None)
    sys.modules.pop("endoreg_db.utils.ai.model_training.config", None)

    config = cast(
        ModelTrainingConfigModule,
        import_module("endoreg_db.utils.ai.model_training.config"),
    )

    assert config.TRAINING_ROOT == data_root.resolve() / "model_training"
    assert "InferenceDataset" not in ai_package.__dict__
    assert "MultiLabelClassificationNet" not in ai_package.__dict__
    assert "Classifier" not in ai_package.__dict__
