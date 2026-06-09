from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.services.media_operation_gate import (
    get_ffmpeg_stream_throttle_state,
)


class Command(BaseCommand):
    help = "Report whether active user streams should throttle the FFmpeg worker."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--mode-only",
            action="store_true",
            help="Print only normal or streaming.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the full throttle-state payload as JSON.",
        )

    def handle(self, *args, **options) -> None:
        mode_only = bool(options["mode_only"])
        json_output = bool(options["json"])

        if mode_only and json_output:
            raise CommandError("--mode-only and --json are mutually exclusive.")

        state = get_ffmpeg_stream_throttle_state()
        if json_output:
            self.stdout.write(json.dumps(state, sort_keys=True))
            return

        self.stdout.write(str(state["mode"]))
