from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from endoreg_db.management.commands.load_base_db_data import Command


class LoadBaseDbDataCommandTests(TestCase):
    @patch("endoreg_db.management.commands.load_base_db_data.call_command")
    def test_skips_legacy_requirement_loader_by_default(self, mocked_call_command):
        command = Command()

        command.handle(verbose=False, include_legacy_requirements=False)

        invoked_commands = [call.args[0] for call in mocked_call_command.call_args_list]
        assert "load_requirement_data" not in invoked_commands

    @patch("endoreg_db.management.commands.load_base_db_data.call_command")
    def test_includes_legacy_requirement_loader_when_flag_is_set(
        self, mocked_call_command
    ):
        command = Command()

        command.handle(verbose=False, include_legacy_requirements=True)

        invoked_commands = [call.args[0] for call in mocked_call_command.call_args_list]
        assert "load_requirement_data" in invoked_commands
