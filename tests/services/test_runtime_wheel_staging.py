from __future__ import annotations

from pathlib import Path

import pytest

from endoreg_db.services.runtime_wheel_staging import reap_runtime_wheel_staging


pytestmark = pytest.mark.no_db


def _wheel(root: Path, version: str, content: bytes = b"wheel") -> Path:
    path = root / f"lx_annotate-{version}-py3-none-any.whl"
    path.write_bytes(content)
    return path


def test_runtime_wheel_staging_defaults_to_dry_run(tmp_path: Path) -> None:
    first = _wheel(tmp_path, "1.0.1", b"old-one")
    second = _wheel(tmp_path, "1.0.2", b"old-two")
    unrelated = tmp_path / "unrelated.whl"
    unrelated.write_bytes(b"keep")

    result = reap_runtime_wheel_staging(runtime_root=tmp_path)

    assert result.mode == "dry_run"
    assert result.candidate_count == 2
    assert result.candidate_bytes == 14
    assert result.removed_count == 0
    assert result.removed_bytes == 0
    assert first.exists()
    assert second.exists()
    assert unrelated.exists()


def test_runtime_wheel_staging_apply_removes_only_valid_candidates(
    tmp_path: Path,
) -> None:
    removed = _wheel(tmp_path, "1.0.1", b"obsolete")
    preserved = _wheel(tmp_path, "1.0.2", b"preserved")
    nested = tmp_path / "data"
    nested.mkdir()
    nested.joinpath("lx_annotate-0.1.0-py3-none-any.whl").write_bytes(b"nested")

    result = reap_runtime_wheel_staging(
        runtime_root=tmp_path,
        apply=True,
        keep_names=frozenset({preserved.name}),
    )

    assert result.mode == "apply"
    assert result.candidate_count == 1
    assert result.removed_count == 1
    assert result.removed_bytes == len(b"obsolete")
    assert not removed.exists()
    assert preserved.exists()
    assert nested.exists()


def test_runtime_wheel_staging_rejects_ambiguous_matching_name(
    tmp_path: Path,
) -> None:
    tmp_path.joinpath("lx_annotate-not-a-wheel.whl").write_bytes(b"ambiguous")

    with pytest.raises(ValueError, match="Ambiguous LX-Annotate wheel"):
        reap_runtime_wheel_staging(runtime_root=tmp_path, apply=True)


def test_runtime_wheel_staging_rejects_matching_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-wheel-target"
    outside.write_bytes(b"must remain")
    candidate = tmp_path / "lx_annotate-1.0.1-py3-none-any.whl"
    candidate.symlink_to(outside)

    with pytest.raises(ValueError, match="not a regular file"):
        reap_runtime_wheel_staging(runtime_root=tmp_path, apply=True)

    assert outside.read_bytes() == b"must remain"
    assert candidate.is_symlink()


def test_runtime_wheel_staging_enforces_bounded_inventory(tmp_path: Path) -> None:
    _wheel(tmp_path, "1.0.1")
    tmp_path.joinpath("data").mkdir()

    with pytest.raises(ValueError, match="more than the bounded 1 entries"):
        reap_runtime_wheel_staging(runtime_root=tmp_path, max_entries=1)
