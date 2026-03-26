"""
Centralized path management for the application.

The module exposes the historical path constants plus a dict-like ``data_paths``
mapping, but uses a Pydantic model as the single source of truth so path
resolution and directory bootstrap stay consistent.
"""

from __future__ import annotations

from collections.abc import Iterable
from logging import getLogger
from pathlib import Path
from typing import ClassVar

from endoreg_db.config.env import env_path
from lx_dtypes.models.base.file.pydantic.FilesAndDirs import FilesAndDirsModel

logger = getLogger(__name__)

PREFIX_RAW = "raw_"

IMPORT_DIR_NAME = "import"
EXPORT_DIR_NAME = "export"

IMPORT_VIDEO_DIR_NAME = "video_import"
REPORT_IMPORT_DIR_NAME = "report_import"

VIDEO_EXPORT_DIR_NAME = "video_export"
REPORT_EXPORT_DIR_NAME = "report_export"

SENSITIVE_VIDEO_DIR_NAME = "sensitive_videos"
SENSITIVE_REPORT_DIR_NAME = "sensitive_reports"
ANONYM_VIDEO_DIR_NAME = "processed_videos_final"
ANONYM_REPORT_DIR_NAME = "processed_reports_final"

RAW_FRAME_DIR_NAME = f"{PREFIX_RAW}frames"
FRAME_DIR_NAME = "frames"
WEIGHTS_DIR_NAME = "model_weights"


class EndoregPathsModel(FilesAndDirsModel):
    """Pydantic-backed container for all application directories."""

    storage: Path
    io: Path
    import_dir: Path
    export_dir: Path
    import_video: Path
    import_report: Path
    video_export: Path
    report_export: Path
    documents: Path
    transcoding: Path
    sensitive_video: Path
    sensitive_report: Path
    anonym_video: Path
    anonym_report: Path
    raw_frame: Path
    frame: Path
    weights: Path
    weights_import: Path
    weights_export: Path
    import_frame: Path
    frame_export: Path

    # If any directory names change, please ensure continued support by changing the values  in key: value.
    legacy_key_map: ClassVar[dict[str, str]] = {
        "storage": "storage",
        "import": "import_dir",
        "import_video": "import_video",
        "sensitive_video": "sensitive_video",
        "sensitive_report": "sensitive_report",
        "anonym_video": "anonym_video",
        "anonym_report": "anonym_report",
        "import_frame": "import_frame",
        "import_report": "import_report",
        "raw_frame": "raw_frame",
        "weights": "weights",
        "weights_import": "weights_import",
        "export": "export_dir",
        "report_export": "report_export",
        "video_export": "video_export",
        "frame_export": "frame_export",
        "weights_export": "weights_export",
        "transcoding": "transcoding",
        "frame": "frame",
        "documents": "documents",
    }

    @classmethod
    def from_environment(cls) -> "EndoregPathsModel":
        storage_dir = env_path("STORAGE_DIR", "internal_storage")
        io_dir = env_path("IO_DIR", "data")

        import_dir = io_dir / IMPORT_DIR_NAME
        export_dir = io_dir / EXPORT_DIR_NAME

        instance = cls(
            dir=storage_dir,
            dirs=[
                storage_dir,
                io_dir,
                import_dir,
                export_dir,
                import_dir / IMPORT_VIDEO_DIR_NAME,
                import_dir / REPORT_IMPORT_DIR_NAME,
                export_dir / VIDEO_EXPORT_DIR_NAME,
                export_dir / REPORT_EXPORT_DIR_NAME,
                storage_dir / "documents",
                storage_dir / "temp",
                storage_dir / SENSITIVE_VIDEO_DIR_NAME,
                storage_dir / SENSITIVE_REPORT_DIR_NAME,
                storage_dir / ANONYM_VIDEO_DIR_NAME,
                storage_dir / ANONYM_REPORT_DIR_NAME,
                storage_dir / RAW_FRAME_DIR_NAME,
                storage_dir / FRAME_DIR_NAME,
                storage_dir / WEIGHTS_DIR_NAME,
                import_dir / WEIGHTS_DIR_NAME,
                export_dir / WEIGHTS_DIR_NAME,
                import_dir / FRAME_DIR_NAME,
                export_dir / FRAME_DIR_NAME,
            ],
            storage=storage_dir,
            io=io_dir,
            import_dir=import_dir,
            export_dir=export_dir,
            import_video=import_dir / IMPORT_VIDEO_DIR_NAME,
            import_report=import_dir / REPORT_IMPORT_DIR_NAME,
            video_export=export_dir / VIDEO_EXPORT_DIR_NAME,
            report_export=export_dir / REPORT_EXPORT_DIR_NAME,
            documents=storage_dir / "documents",
            transcoding=storage_dir / "temp",
            sensitive_video=storage_dir / SENSITIVE_VIDEO_DIR_NAME,
            sensitive_report=storage_dir / SENSITIVE_REPORT_DIR_NAME,
            anonym_video=storage_dir / ANONYM_VIDEO_DIR_NAME,
            anonym_report=storage_dir / ANONYM_REPORT_DIR_NAME,
            raw_frame=storage_dir / RAW_FRAME_DIR_NAME,
            frame=storage_dir / FRAME_DIR_NAME,
            weights=storage_dir / WEIGHTS_DIR_NAME,
            weights_import=import_dir / WEIGHTS_DIR_NAME,
            weights_export=export_dir / WEIGHTS_DIR_NAME,
            import_frame=import_dir / FRAME_DIR_NAME,
            frame_export=export_dir / FRAME_DIR_NAME,
        )
        instance.ensure_directories()
        return instance

    def ensure_directories(self) -> None:
        for path in self.dirs:
            path.mkdir(parents=True, exist_ok=True)
            logger.info("Path ready: %s", path.resolve())

    def as_dict(self) -> dict[str, Path]:
        return {key: self[key] for key in self.legacy_key_map}

    def __getitem__(self, key: str) -> Path:
        try:
            field_name = self.legacy_key_map[key]
        except KeyError as exc:
            raise KeyError(f"Unknown data path key: {key}") from exc
        return getattr(self, field_name)

    def __len__(self) -> int:
        return len(self.legacy_key_map)

    def keys(self) -> Iterable[str]:
        return self.legacy_key_map.keys()

    def items(self) -> Iterable[tuple[str, Path]]:
        return ((key, self[key]) for key in self.legacy_key_map)

    def values(self) -> Iterable[Path]:
        return (self[key] for key in self.legacy_key_map)


EndoregPathsModel.model_rebuild()

data_paths_model = EndoregPathsModel.from_environment()
data_paths = data_paths_model

STORAGE_DIR = data_paths_model.storage
IO_DIR = data_paths_model.io

IMPORT_DIR = data_paths_model.import_dir
EXPORT_DIR = data_paths_model.export_dir

IMPORT_VIDEO_DIR = data_paths_model.import_video
IMPORT_REPORT_DIR = data_paths_model.import_report

VIDEO_EXPORT_DIR = data_paths_model.video_export
REPORT_EXPORT_DIR = data_paths_model.report_export

DOCUMENT_DIR = data_paths_model.documents
TRANSCODING_DIR = data_paths_model.transcoding

ANONYM_VIDEO_DIR = data_paths_model.anonym_video
SENSITIVE_VIDEO_DIR = data_paths_model.sensitive_video
ANONYM_REPORT_DIR = data_paths_model.anonym_report
SENSITIVE_REPORT_DIR = data_paths_model.sensitive_report

FRAME_DIR = data_paths_model.frame
WEIGHTS_DIR = data_paths_model.weights
RAW_FRAME_DIR = data_paths_model.raw_frame

WEIGHTS_IMPORT_DIR = data_paths_model.weights_import
WEIGHTS_EXPORT_DIR = data_paths_model.weights_export

FRAME_IMPORT_DIR = data_paths_model.import_frame
FRAME_EXPORT_DIR = data_paths_model.frame_export

logger.info("Storage directory: %s", STORAGE_DIR.resolve())
logger.info("Export directory: %s", EXPORT_DIR.resolve())


def to_storage_relative(path: str | Path) -> str:
    """
    Return a path string relative to STORAGE_DIR, suitable for Django FileField.name.

    If ``path`` is outside STORAGE_DIR, it is returned unchanged.
    """
    original_path = str(path)
    resolved_path = Path(path)
    storage_root = STORAGE_DIR.resolve()

    if not resolved_path.is_absolute():
        resolved_path = resolved_path.resolve()

    try:
        relative_path = resolved_path.relative_to(storage_root)
    except ValueError:
        return original_path

    return relative_path.as_posix()
