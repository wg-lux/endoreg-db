from __future__ import annotations

import tomllib
from pathlib import Path

from workflow import packaged_workflow_root
from workflow.scripts.import_common import WorkflowConfig


def test_top_level_workflow_package_exposes_complete_assets() -> None:
    root = packaged_workflow_root()

    assert root.name == "workflow"
    assert root.joinpath("Snakefile").is_file()
    assert root.joinpath("profiles/offline-batch/config.yaml").is_file()
    assert root.joinpath("rules/video_import.smk").is_file()
    assert root.joinpath("rules/video_transcode.smk").is_file()
    assert root.joinpath("rules/video_hls_materialization.smk").is_file()
    assert root.joinpath("scripts/run_video_import.py").is_file()
    assert WorkflowConfig.__module__ == "workflow.scripts.import_common"


def test_maturin_wheel_declares_workflow_package_and_non_python_assets() -> None:
    project_root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads(
        project_root.joinpath("pyproject.toml").read_text(encoding="utf-8")
    )
    maturin = pyproject["tool"]["maturin"]

    assert "workflow" in maturin["python-packages"]
    assert "workflow/Snakefile" in maturin["include"]
    assert "workflow/rules/**/*.smk" in maturin["include"]
    assert "workflow/profiles/**/*.yaml" in maturin["include"]
