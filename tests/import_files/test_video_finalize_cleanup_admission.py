from pathlib import Path
from unittest.mock import Mock

import pytest

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage import state_management
from endoreg_db.schemas.processed_video_cleanup import cleanup_receipts
from endoreg_db.services import processed_video_cleanup
from tests.services.test_processed_video_cleanup import (
    Replacement,
    replacement as replacement_fixture,
)

replacement = replacement_fixture
pytestmark = pytest.mark.django_db


def _context(replacement: Replacement, tmp_path: Path) -> ImportContext:
    candidate = tmp_path / "next-anonymized.mp4"
    candidate.write_bytes(b"next candidate")
    ctx = ImportContext(file_path=candidate, center_name="cleanup", file_type="video")
    ctx.current_video = replacement.video
    ctx.anonymized_path = candidate
    return ctx


def test_finalize_retries_persisted_cleanup_without_dropping_new_context_metadata(
    replacement: Replacement, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(replacement, tmp_path)
    video = replacement.video
    # This context predates the persisted cleanup receipt, but includes new import data.
    video.meta = {"processed_generation_cleanup": [], "new_import_hint": "preserve"}
    admitted = Mock()
    monkeypatch.setattr(state_management, "_finalize_video_success_owned", admitted)

    state_management.finalize_video_success(ctx)

    admitted.assert_called_once_with(ctx, video)
    assert not video.processed_file.storage.exists(replacement.old_name)
    assert not replacement.old_hls.exists()
    updated_meta = video.meta
    assert updated_meta is not None
    assert updated_meta["new_import_hint"] == "preserve"
    assert cleanup_receipts(video.meta) == []
    assert ctx.anonymized_path is not None and ctx.anonymized_path.is_file()


def test_finalize_cleanup_failure_blocks_candidate_allocation(
    replacement: Replacement, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _context(replacement, tmp_path)
    store = Mock()
    monkeypatch.setattr(state_management, "_store_existing_final_file", store)
    cleanup = Mock(side_effect=OSError("previous master deletion denied"))
    monkeypatch.setattr(processed_video_cleanup, "safe_delete_field_file", cleanup)

    with pytest.raises(OSError, match="previous master deletion denied"):
        state_management.finalize_video_success(ctx)

    store.assert_not_called()
    assert replacement.video.processed_file.storage.exists(replacement.old_name)
    assert ctx.anonymized_path is not None and ctx.anonymized_path.is_file()
    replacement.video.refresh_from_db()
    assert cleanup_receipts(replacement.video.meta)


def test_finalize_uncommitted_cleanup_blocks_candidate_allocation(
    replacement: Replacement, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = replacement.video
    receipts = cleanup_receipts(video.meta)
    video.meta = {
        **(video.meta or {}),
        "processed_generation_cleanup": [
            receipt.model_copy(update={"committed": False}).model_dump(mode="json")
            for receipt in receipts
        ],
    }
    video.save(update_fields=["meta"])
    ctx = _context(replacement, tmp_path)
    store = Mock()
    monkeypatch.setattr(state_management, "_store_existing_final_file", store)

    with pytest.raises(RuntimeError, match="replacement_not_committed"):
        state_management.finalize_video_success(ctx)

    store.assert_not_called()
    assert video.processed_file.storage.exists(replacement.old_name)
    assert ctx.anonymized_path is not None and ctx.anonymized_path.is_file()
