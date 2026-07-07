from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.services.hub.transfer_client import HubTransferClient


class Command(BaseCommand):
    help = "Check status of a remote hub transfer."

    def add_arguments(self, parser):
        parser.add_argument("--target-url", required=True)
        parser.add_argument("--source-node-key", required=True)
        parser.add_argument("--source-node-secret-file", required=True)
        parser.add_argument("--transfer-key", required=True)
        parser.add_argument("--insecure-skip-tls-verify", action="store_true")

    def handle(self, *args, **options):
        secret_path = Path(options["source_node_secret_file"]).expanduser().resolve()
        if not secret_path.is_file():
            raise CommandError(f"Secret file not found: {secret_path}")

        client = HubTransferClient(
            base_url=options["target_url"],
            node_key=options["source_node_key"],
            node_secret=secret_path.read_text(encoding="utf-8").strip(),
            verify_tls=not options["insecure_skip_tls_verify"],
        )

        status = client.get_status(options["transfer_key"])
        self.stdout.write(str(status))