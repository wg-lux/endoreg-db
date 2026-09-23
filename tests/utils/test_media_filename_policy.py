from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.video_files.io import get_target_anonymized_video_path
from endoreg_db.utils.paths import clear_runtime_paths_cache, get_runtime_paths
from endoreg_db.utils.storage.files import canonical_media_name


@pytest.mark.parametrize("suffix", [".pdf", ".PDF", ".mp4", ".MP4", ".avi"])
def test_media_filename_uses_one_policy(suffix: str) -> None:
    assert canonical_media_name("a" * 64, suffix) == "a" * 64 + suffix.lower()
    assert canonical_media_name("a" * 64, suffix, generation="version-2") == (
        "a" * 64 + ".version-2" + suffix.lower()
    )


@pytest.mark.parametrize("identity", ["", "../escape", "/absolute", "a.b", "a b", "ä"])
def test_media_filename_rejects_unsafe_identity(identity: str) -> None:
    with pytest.raises(ValueError):
        canonical_media_name(identity, ".pdf")


@pytest.mark.parametrize("suffix", ["", "pdf", ".", ".pdf/escape", ".tar.pdf"])
def test_media_filename_rejects_unsafe_suffix(suffix: str) -> None:
    with pytest.raises(ValueError):
        canonical_media_name("identity", suffix)


def test_video_target_resolves_runtime_root_at_use_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The root, where paths model will resolve storage to should not error when removing the env var cache"""
    video = cast(VideoFile, SimpleNamespace(raw_video_hash="identity"))
    try:
        for root in (tmp_path / "first", tmp_path / "second"):
            monkeypatch.setenv("LX_RUNTIME_ROOT", str(root))
            clear_runtime_paths_cache()
            assert get_target_anonymized_video_path(video) == (
                get_runtime_paths().anonym_video
                / canonical_media_name("identity", ".mp4")
            )
    finally:
        clear_runtime_paths_cache()
