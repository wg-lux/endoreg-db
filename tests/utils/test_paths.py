import importlib
import uuid
from pathlib import Path

from endoreg_db.config.env import BASE_DIR
from endoreg_db.utils import paths as paths_module


def test_data_paths_behaves_like_a_mapping():
    expanded = {**paths_module.data_paths}

    assert expanded["storage"] == paths_module.STORAGE_DIR
    assert expanded["import_video"] == paths_module.IMPORT_VIDEO_DIR
    assert expanded["import_preanonymized"] == paths_module.IMPORT_PREANONYMIZED_DIR
    assert (
        expanded["import_anonymized_video"] == paths_module.IMPORT_ANONYMIZED_VIDEO_DIR
    )
    assert (
        expanded["import_anonymized_report"]
        == paths_module.IMPORT_ANONYMIZED_REPORT_DIR
    )
    assert expanded["anonym_video"] == paths_module.ANONYM_VIDEO_DIR
    assert expanded["documents"] == paths_module.DOCUMENT_DIR


def test_paths_module_resolves_relative_env_paths(monkeypatch):
    unique_suffix = uuid.uuid4().hex[:8]
    storage_rel = f"data/tests/runtime/{unique_suffix}/storage"
    io_rel = f"data/tests/runtime/{unique_suffix}/io"

    with monkeypatch.context() as scoped:
        scoped.setenv("STORAGE_DIR", storage_rel)
        scoped.setenv("IO_DIR", io_rel)

        reloaded = importlib.reload(paths_module)

        expected_storage = (BASE_DIR / storage_rel).resolve()
        expected_io = (BASE_DIR / io_rel).resolve()

        assert reloaded.STORAGE_DIR == expected_storage
        assert reloaded.IO_DIR == expected_io
        assert reloaded.IMPORT_VIDEO_DIR == expected_io / "import" / "video_import"
        assert (
            reloaded.IMPORT_PREANONYMIZED_DIR
            == expected_io / "import" / "preanonymized_import"
        )
        assert (
            reloaded.IMPORT_ANONYMIZED_VIDEO_DIR
            == expected_io / "import" / "anonymized_video_import"
        )
        assert (
            reloaded.IMPORT_ANONYMIZED_REPORT_DIR
            == expected_io / "import" / "anonymized_report_import"
        )
        assert reloaded.ANONYM_VIDEO_DIR == expected_storage / "processed_videos_final"

        for path in (
            reloaded.STORAGE_DIR,
            reloaded.IO_DIR,
            reloaded.IMPORT_DIR,
            reloaded.EXPORT_DIR,
            reloaded.IMPORT_VIDEO_DIR,
            reloaded.IMPORT_REPORT_DIR,
            reloaded.IMPORT_PREANONYMIZED_DIR,
            reloaded.IMPORT_ANONYMIZED_VIDEO_DIR,
            reloaded.IMPORT_ANONYMIZED_REPORT_DIR,
            reloaded.ANONYM_VIDEO_DIR,
            reloaded.SENSITIVE_VIDEO_DIR,
        ):
            assert isinstance(path, Path)
            assert path.exists()
            assert path.is_dir()

    importlib.reload(paths_module)
