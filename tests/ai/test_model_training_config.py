from __future__ import annotations

import importlib
import os

from django.test import override_settings

from endoreg_db.utils.ai.model_training import config as model_training_config


def test_model_training_paths_use_runtime_data_dir_without_import_mkdir(
    monkeypatch,
    tmp_path,
) -> None:
    original_data_dir = os.environ.get("DATA_DIR")
    data_root = tmp_path / "runtime-data"
    fake_base_dir = tmp_path / "site-packages"

    try:
        monkeypatch.setenv("DATA_DIR", str(data_root))

        with override_settings(BASE_DIR=fake_base_dir):
            reloaded = importlib.reload(model_training_config)

        assert reloaded.BASE_DIR == fake_base_dir
        assert reloaded.TRAINING_ROOT == data_root.resolve() / "model_training"
        assert reloaded.CHECKPOINTS_DIR == reloaded.TRAINING_ROOT / "checkpoints"
        assert reloaded.RUNS_DIR == reloaded.TRAINING_ROOT / "runs"
        assert not (fake_base_dir / "data").exists()
        assert not reloaded.TRAINING_ROOT.exists()

        reloaded.ensure_training_directories()

        assert reloaded.TRAINING_ROOT.is_dir()
        assert reloaded.CHECKPOINTS_DIR.is_dir()
        assert reloaded.RUNS_DIR.is_dir()
    finally:
        if original_data_dir is None:
            monkeypatch.delenv("DATA_DIR", raising=False)
        else:
            monkeypatch.setenv("DATA_DIR", original_data_dir)
        importlib.reload(model_training_config)
