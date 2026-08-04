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
        parser.add_argument("--ca-file", default="")
        parser.add_argument("--client-certificate-file", default="")
        parser.add_argument("--client-key-file", default="")

    def handle(self, *args, **options):
        secret_path = Path(options["source_node_secret_file"]).expanduser().resolve()

        if not secret_path.is_file():
            raise CommandError(f"Secret file not found: {secret_path}")

        node_secret = secret_path.read_text(encoding="utf-8").strip()

        if not node_secret:
            raise CommandError(f"Secret file is empty: {secret_path}")

        ca_path = (
            Path(options["ca_file"]).expanduser().resolve()
            if options["ca_file"].strip()
            else None
        )

        client_certificate_path = (
            Path(options["client_certificate_file"]).expanduser().resolve()
            if options["client_certificate_file"].strip()
            else None
        )

        client_key_path = (
            Path(options["client_key_file"]).expanduser().resolve()
            if options["client_key_file"].strip()
            else None
        )

        if (client_certificate_path is None) != (client_key_path is None):
            raise CommandError(
                "--client-certificate-file and --client-key-file "
                "must be provided together"
            )

        for label, configured_path in (
            ("CA bundle", ca_path),
            ("mTLS client certificate", client_certificate_path),
            ("mTLS client private key", client_key_path),
        ):
            if configured_path is not None and not configured_path.is_file():
                raise CommandError(f"{label} not found: {configured_path}")

        verify_tls: bool | str

        if options["insecure_skip_tls_verify"]:
            verify_tls = False
        elif ca_path is not None:
            verify_tls = str(ca_path)
        else:
            verify_tls = True

        client = HubTransferClient(
            base_url=options["target_url"],
            node_key=options["source_node_key"],
            node_secret=node_secret,
            verify_tls=verify_tls,
            client_certificate_file=client_certificate_path,
            client_key_file=client_key_path,
        )

        status = client.get_status(options["transfer_key"])

        self.stdout.write(str(status))
