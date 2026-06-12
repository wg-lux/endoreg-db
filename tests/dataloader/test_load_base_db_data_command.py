from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase

from endoreg_db.management.commands.load_base_db_data import Command
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)


def _warning_style(message: str) -> str:
    return message




class LoadBaseDbDataCommandTests(TestCase):
    @patch.object(Command, "_endoreg_db_schema_is_ready", return_value=False)
    @patch("endoreg_db.management.commands.load_base_db_data.call_command")
    def test_skips_all_subcommands_when_schema_is_not_ready(
        self,
        mocked_call_command: MagicMock,
        mocked_schema_ready: MagicMock,
    ) -> None:
        command = Command()

        with (
            patch.object(command.stdout, "write") as mocked_write,
            patch.object(command.style, "WARNING", side_effect=_warning_style),
        ):
            options = VerboseManagementCommandOptionsPayload(verbose=False)
            command.handle(**options.model_dump(mode="python"))

        mocked_schema_ready.assert_called_once_with()
        mocked_call_command.assert_not_called()
        mocked_write.assert_any_call(
            "Skipping base data load because endoreg_db migrations have not been applied yet."
        )

    @patch("endoreg_db.management.commands.load_base_db_data.call_command")
    def test_never_invokes_legacy_requirement_loader(
        self,
        mocked_call_command: MagicMock,
    ) -> None:
        command = Command()

        with patch.object(Command, "_endoreg_db_schema_is_ready", return_value=True):
            options = VerboseManagementCommandOptionsPayload(verbose=False)
            command.handle(**options.model_dump(mode="python"))

        invoked_commands = [
            str(args[0])
            for args, _kwargs in mocked_call_command.call_args_list
            if args
        ]
        assert "load_requirement_data" not in invoked_commands
