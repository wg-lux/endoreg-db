from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from lx_dtypes.models import SensitiveMeta
from pydantic import ValidationError

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile


@pytest.mark.unit
def test_import_context_coerces_paths_without_computing_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source-payload")

    ctx = ImportContext.model_validate(
        {
            "file_path": str(source),
            "center_name": " test-center ",
            "file_type": "report",
            "original_path": str(source),
        }
    )

    assert ctx.file_path == source
    assert ctx.original_path == source
    assert ctx.center_name == "test-center"
    assert ctx.file_hash is None
    assert isinstance(ctx.extracted_metadata, SensitiveMeta)


@pytest.mark.unit
def test_import_context_uses_independent_default_dicts(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-payload")
    first_ctx = ImportContext(file_path=source, center_name="center", file_type="video")
    second_ctx = ImportContext(
        file_path=source,
        center_name="center",
        file_type="video",
    )

    first_ctx.validated_raw_source_stream["width"] = 1920

    assert second_ctx.validated_raw_source_stream == {}


@pytest.mark.unit
def test_import_context_preserves_mutable_runtime_handles(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"source-payload")

    ctx = ImportContext(file_path=source, center_name="center", file_type="video")
    current_video = object()

    cast(Any, ctx).current_video = current_video
    ctx.anonymized_path = output
    ctx.file_hash = "test-hash"

    assert ctx.current_video is current_video
    assert ctx.anonymized_path == output
    assert ctx.file_hash == "test-hash"


@pytest.mark.unit
def test_import_context_rejects_positional_construction(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source-payload")

    with pytest.raises(ValidationError):
        ImportContext.model_validate((source, "center"))


@pytest.mark.unit
def test_import_context_rejects_loose_metadata_and_unknown_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source-payload")
    ctx = ImportContext(file_path=source, center_name="center", file_type="report")

    with pytest.raises(ValidationError, match="extracted_metadata"):
        setattr(ctx, "extracted_metadata", {})

    with pytest.raises(ValidationError, match="extra"):
        ImportContext.model_validate(
            {
                "file_path": source,
                "center_name": "center",
                "file_type": "report",
                "unexpected": True,
            }
        )


@pytest.mark.unit
def test_import_context_rejects_invalid_file_type(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source-payload")

    with pytest.raises(ValidationError, match="file_type"):
        ImportContext(file_path=source, center_name="center", file_type="report")


@pytest.mark.parametrize("report", [False, True])
def test_import_context_instance_tracks_replacement_and_clear(report: bool) -> None:
    ctx = ImportContext(file_path=Path("source.pdf"), center_name="center")
    field = "current_report" if report else "current_video"
    first = RawPdfFile() if report else VideoFile()
    replacement = RawPdfFile() if report else VideoFile()
    setattr(ctx, field, first)
    assert ctx.instance is first
    setattr(ctx, field, replacement)
    assert ctx.instance is replacement
    setattr(ctx, field, None)
    assert ctx.instance is None


@pytest.mark.parametrize("report_first", [False, True])
def test_import_context_rejects_competing_media_without_changing_binding(
    report_first: bool,
) -> None:
    ctx = ImportContext(file_path=Path("source.pdf"), center_name="center")
    field = "current_report" if report_first else "current_video"
    other = "current_video" if report_first else "current_report"
    instance = RawPdfFile() if report_first else VideoFile()
    conflicting = VideoFile() if report_first else RawPdfFile()
    setattr(ctx, field, instance)
    with pytest.raises(ValidationError, match="only one media instance"):
        setattr(ctx, other, conflicting)
    assert ctx.instance is instance
    assert getattr(ctx, other) is None


def test_import_context_rejects_duplicate_instance_input() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ImportContext.model_validate(
            {
                "file_path": "source.mp4",
                "center_name": "center",
                "instance": VideoFile(),
            }
        )

    with pytest.raises(ValidationError, match="only one media instance"):
        ImportContext(
            file_path=Path("source.mp4"),
            center_name="center",
            current_video=VideoFile(),
            current_report=RawPdfFile(),
        )


@pytest.mark.no_db
def test_import_context_does_not_choose_processor_for_report() -> None:
    ctx = ImportContext(
        file_path=Path("source.pdf"), center_name="center", file_type="report"
    )
    assert ctx.processor_name is None
    with pytest.raises(ValidationError, match="must not be empty"):
        ctx.processor_name = " "
    assert ctx.processor_name is None
