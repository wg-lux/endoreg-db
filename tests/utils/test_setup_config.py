"""The packaged bootstrap YAML is the single validated configuration source."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from lx_dtypes.models.contracts.setup_config import SetupConfigDataPayload
from lx_dtypes.utils.study_setup_yaml import parse_setup_yaml

from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.setup_config import SetupConfig

pytestmark = pytest.mark.no_db


def write(path: Path, content: str) -> Path:
    atomic_write_file(destination=path, content=[content.encode()])
    return path


def test_default_config_uses_bundled_yaml_and_canonical_weights_directory() -> None:
    config = SetupConfig()
    canonical = SetupConfigDataPayload.model_validate(
        parse_setup_yaml(config.config_file.read_text())
    )

    assert config.config_file.parts[-3:] == ("endoreg_db", "data", "setup_config.yaml")
    assert (
        config.get_primary_model_name()
        == canonical.default_models.primary_classification_model
    )
    assert config.get_auto_generation_defaults() == canonical.auto_generation_defaults
    assert config.get_weights_search_dirs()[0] == get_runtime_paths().weights
    assert all("$" not in str(path) for path in config.get_weights_search_dirs())


def test_missing_explicit_config_fails_instead_of_selecting_another_model(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        SetupConfig(tmp_path / "missing.yml")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        "unexpected: true",
        "key: 1\nkey: 2",
        "key: !!python/object:builtins.object {}",
        "key: [",
    ],
)
def test_invalid_config_is_never_replaced_by_defaults(tmp_path: Path, raw: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        SetupConfig(write(tmp_path / "invalid.yml", raw))


def test_explicit_config_changes_runtime_selection(tmp_path: Path) -> None:
    packaged = SetupConfig()
    raw = packaged.config_file.read_text().replace(
        'primary_labelset: "multilabel_classification_colonoscopy_default"',
        'primary_labelset: "reviewed_labels"',
    )

    config = SetupConfig(write(tmp_path / "setup.yml", raw))

    assert config.get_primary_labelset_name() == "reviewed_labels"


def test_missing_weights_variable_fails_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SCHEMA_TEST_WEIGHTS", raising=False)
    raw = (
        SetupConfig()
        .config_file.read_text()
        .replace('  - "tests/assets"', '  - "${SCHEMA_TEST_WEIGHTS}/weights"')
    )
    config = SetupConfig(write(tmp_path / "setup.yml", raw))

    with pytest.raises(ValueError, match="Unresolved environment variable"):
        config.get_weights_search_dirs()


def test_packaged_model_specific_hints_are_projected_into_their_shared_contract() -> (
    None
):
    config = SetupConfig()
    result = config.get_model_specific_config(config.get_primary_model_name())

    assert result is not None
    assert result.setup_config["is_primary_model"] is True
    assert config.get_model_specific_config("no_such_model") is None


def test_malformed_model_hints_raise_instead_of_disappearing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import endoreg_db.data as data

    monkeypatch.setattr(data, "AI_MODEL_META_DATA_DIR", tmp_path)
    write(tmp_path / "invalid.yaml", "- fields: {name: example}\n  setup_config: []")

    with pytest.raises(ValidationError):
        SetupConfig().get_model_specific_config("example")


def test_runtime_weights_take_priority_and_extra_directories_are_deduplicated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from endoreg_db.utils.paths import clear_runtime_paths_cache

    monkeypatch.setenv("LX_RUNTIME_ROOT", str(tmp_path / "runtime"))
    clear_runtime_paths_cache()
    try:
        monkeypatch.setenv("SCHEMA_TEST_WEIGHTS", str(tmp_path / "extra"))
        raw = (
            SetupConfig()
            .config_file.read_text()
            .replace(
                '  - "tests/assets"',
                '  - "${SCHEMA_TEST_WEIGHTS}"\n  - "${SCHEMA_TEST_WEIGHTS}"',
            )
        )
        config = SetupConfig(write(tmp_path / "setup.yml", raw))
        runtime_file = write(
            get_runtime_paths().weights
            / "colo_segmentation_RegNetX800MF_runtime.safetensors",
            "",
        )
        extra_file = write(
            tmp_path / "extra" / "colo_segmentation_RegNetX800MF_extra.safetensors", ""
        )

        found = config.find_model_weights_files()

        assert found[:2] == [runtime_file, extra_file]
        assert config.get_weights_search_dirs().count(tmp_path / "extra") == 1
    finally:
        clear_runtime_paths_cache()
