from pathlib import Path

import pytest
from lx_dtypes.models import SensitiveMeta
from pydantic import ValidationError

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.utils.filesystem.file_operations import sha256_file


@pytest.mark.unit
def test_import_context_computes_hash_and_coerces_paths(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source-payload")

    ctx = ImportContext(
        file_path=str(source),
        center_name=" test-center ",
        file_type="report",
        original_path=str(source),
    )

    assert ctx.file_path == source
    assert ctx.original_path == source
    assert ctx.center_name == "test-center"
    assert ctx.file_hash == sha256_file(source)
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

    ctx.current_video = current_video
    ctx.anonymized_path = str(output)
    ctx.file_hash = "test-hash"

    assert ctx.current_video is current_video
    assert ctx.anonymized_path == output
    assert ctx.file_hash == "test-hash"


@pytest.mark.unit
def test_import_context_rejects_positional_construction(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source-payload")

    with pytest.raises(TypeError):
        ImportContext(source, "center")


@pytest.mark.unit
def test_import_context_rejects_loose_metadata_and_unknown_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source-payload")
    ctx = ImportContext(file_path=source, center_name="center", file_type="report")

    with pytest.raises(ValidationError, match="extracted_metadata"):
        ctx.extracted_metadata = {}

    with pytest.raises(ValidationError, match="extra"):
        ImportContext(
            file_path=source,
            center_name="center",
            file_type="report",
            unexpected=True,
        )


@pytest.mark.unit
def test_import_context_rejects_invalid_file_type(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source-payload")

    with pytest.raises(ValidationError, match="file_type"):
        ImportContext(file_path=source, center_name="center", file_type="pdf")
