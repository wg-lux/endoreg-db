from __future__ import annotations

import subprocess
from unittest.mock import Mock

import pytest

from endoreg_db import checks
from endoreg_db.utils.video import executable_discovery as discovery

pytestmark = pytest.mark.no_db


def _tool_path(name: str) -> str:
    return f"/tools/{name}"


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
def test_media_preflight_requires_both_tools(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    def find_tool(name: str) -> str | None:
        return None if name == missing else _tool_path(name)

    monkeypatch.setattr(
        discovery.shutil,
        "which",
        find_tool,
    )
    # Existing resolvers may find overrides or Nix store binaries that bare
    # subprocess calls in the compute package cannot reach through PATH.
    monkeypatch.setattr(
        discovery, "resolve_ffmpeg_executable", lambda: "/nix/store/ffmpeg/bin/ffmpeg"
    )
    monkeypatch.setattr(
        discovery, "resolve_ffprobe_executable", lambda: "/nix/store/ffmpeg/bin/ffprobe"
    )
    run = Mock()
    monkeypatch.setattr(discovery.subprocess, "run", run)

    with pytest.raises(RuntimeError, match=f"{missing} is unavailable.*worker"):
        discovery.assert_media_tools_available()
    assert run.call_count == (0 if missing == "ffmpeg" else 1)


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("removed executable"),
        PermissionError("not executable"),
        subprocess.CalledProcessError(1, "ffprobe"),
        subprocess.TimeoutExpired("ffprobe", 10),
    ],
)
@pytest.mark.parametrize("failed_tool", ["ffmpeg", "ffprobe"])
def test_media_preflight_rejects_unusable_tools(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, failed_tool: str
) -> None:
    monkeypatch.setattr(discovery.shutil, "which", _tool_path)
    run = Mock(side_effect=[failure] if failed_tool == "ffmpeg" else [None, failure])
    monkeypatch.setattr(discovery.subprocess, "run", run)

    with pytest.raises(RuntimeError, match=f"{failed_tool} failed its version check"):
        discovery.assert_media_tools_available()


def test_media_preflight_executes_both_tools_with_bounded_silent_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery.shutil, "which", _tool_path)
    run = Mock()
    monkeypatch.setattr(discovery.subprocess, "run", run)

    discovery.assert_media_tools_available()

    assert run.call_count == 2
    for tool in ("ffmpeg", "ffprobe"):
        run.assert_any_call(
            [f"/tools/{tool}", "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )


@pytest.mark.parametrize("available", [False, True])
def test_media_deployment_check_reports_actionable_error(
    monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    preflight = Mock(
        side_effect=None if available else RuntimeError("ffprobe unavailable")
    )
    monkeypatch.setattr(checks, "assert_media_tools_available", preflight)

    messages = checks.check_media_runtime_configuration()

    preflight.assert_called_once_with()
    assert [message.id for message in messages] == (
        [] if available else ["endoreg_db.E004"]
    )
    if not available:
        assert messages[0].msg == "ffprobe unavailable"
