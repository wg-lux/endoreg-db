from __future__ import annotations

from typing import TypedDict, Unpack

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandParser
from lx_dtypes.models.contracts.management_command import (
    VerboseManagementCommandOptionsPayload,
)


class LoadUserGroupsCommandOptions(TypedDict):
    verbose: bool


class Command(BaseCommand):
    help = "Create additional user groups and permissions for all models in 'endoreg_db' app."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Display verbose output",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[LoadUserGroupsCommandOptions],
    ) -> None:
        verbose = VerboseManagementCommandOptionsPayload.model_validate(options).verbose

        # Create groups
        groups: list[str] = [
            "demo",
            "verified",
            "agl",
            "endo_reg_user",
            "g_play_user",
            "ukw_user",
        ]
        for group_name in groups:
            _group, created = Group.objects.get_or_create(name=group_name)
            if verbose and created:
                self.stdout.write(self.style.SUCCESS(f"Created group {group_name}"))

        if verbose:
            self.stdout.write(self.style.SUCCESS("All groups processed successfully."))
