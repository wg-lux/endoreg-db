"""Centralized service modules for background and heavy job orchestration."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any

_JOB_MODULES = {
    "frame_extraction_jobs": ".frame_extraction_jobs",
    "heavy_jobs": ".heavy_jobs",
    "model_training_jobs": ".model_training_jobs",
    "report_llm_jobs": ".report_llm_jobs",
    "video_post_validation_jobs": ".video_post_validation_jobs",
    "video_correction_jobs": ".video_correction_jobs",
    "video_reimport_jobs": ".video_reimport_jobs",
    "video_task_cleanup": ".video_task_cleanup",
}

__all__ = [
    "frame_extraction_jobs",
    "heavy_jobs",
    "model_training_jobs",
    "report_llm_jobs",
    "video_post_validation_jobs",
    "video_correction_jobs",
    "video_reimport_jobs",
    "video_task_cleanup",
]

if TYPE_CHECKING:
    frame_extraction_jobs: ModuleType
    heavy_jobs: ModuleType
    model_training_jobs: ModuleType
    report_llm_jobs: ModuleType
    video_post_validation_jobs: ModuleType
    video_correction_jobs: ModuleType
    video_reimport_jobs: ModuleType
    video_task_cleanup: ModuleType


def __getattr__(name: str) -> Any:
    module_path = _JOB_MODULES.get(name)
    if module_path is not None:
        module = import_module(module_path, __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
