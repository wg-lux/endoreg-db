from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from endoreg_db.management.commands.load_base_db_data import Command


class LoadBaseDbDataCommandTests(TestCase):
    @patch.object(Command, "_endoreg_db_schema_is_ready", return_value=False)
    @patch("endoreg_db.management.commands.load_base_db_data.call_command")
    def test_skips_all_subcommands_when_schema_is_not_ready(
        self,
        mocked_call_command,
        mocked_schema_ready,
    ):
        command = Command()

        with (
            patch.object(command.stdout, "write") as mocked_write,
            patch.object(command.style, "WARNING", side_effect=lambda message: message),
        ):
            command.handle(verbose=False)

        mocked_schema_ready.assert_called_once_with()
        mocked_call_command.assert_not_called()
        mocked_write.assert_any_call(
            "Skipping base data load because endoreg_db migrations have not been applied yet."
        )

    @patch("endoreg_db.management.commands.load_base_db_data.call_command")
    def test_never_invokes_legacy_requirement_loader(self, mocked_call_command):
        command = Command()

        with patch.object(Command, "_endoreg_db_schema_is_ready", return_value=True):
            command.handle(verbose=False)

        invoked_commands = [call.args[0] for call in mocked_call_command.call_args_list]
        assert "load_requirement_data" not in invoked_commands
