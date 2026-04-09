from __future__ import annotations

from io import StringIO

from django.core.management import call_command
import pytest


class StartFileWatcherCommandTests:
    def test_start_filewatcher_uses_packaged_service(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from endoreg_db.management.commands import start_filewatcher as command_module

        calls: list[str] = []

        class FakeWatcherService:
            def _validate_django_setup(self) -> None:
                calls.append("validated")

        monkeypatch.setattr(command_module, "FileWatcherService", FakeWatcherService)
        stdout = StringIO()
        call_command(
            "start_filewatcher",
            test=True,
            log_level="INFO",
            stdout=stdout,
        )

        assert calls == ["validated"]
        assert "File watcher test passed" in stdout.getvalue()
