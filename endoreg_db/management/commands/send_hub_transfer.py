from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hub.transfer_client import HubTransferClient
from endoreg_db.services.hub.transfer_payloads import (
    build_report_transfer_payload,
    build_video_transfer_payload,
)


class Command(BaseCommand):
    help = "Send anonymized processed video/report data to another endoreg-db node."

    def add_arguments(self, parser):
        parser.add_argument("--target-url", required=True)
        parser.add_argument("--source-node-key", required=True)
        parser.add_argument("--source-node-secret-file", required=True)
        parser.add_argument("--target-node-key", required=True)
        parser.add_argument("--source-center-key", default="")
        parser.add_argument("--resource-kind", choices=["video", "report"], required=True)
        parser.add_argument("--object-id", type=int, required=True)
        parser.add_argument("--transfer-key", default="")
        parser.add_argument("--metadata-only", action="store_true")
        parser.add_argument("--insecure-skip-tls-verify", action="store_true")

    def handle(self, *args, **options):
        secret_path = Path(options["source_node_secret_file"]).expanduser().resolve()
        if not secret_path.is_file():
            raise CommandError(f"Secret file not found: {secret_path}")

        node_secret = secret_path.read_text(encoding="utf-8").strip()
        if not node_secret:
            raise CommandError("Node secret file is empty")

        resource_kind = options["resource_kind"]
        object_id = options["object_id"]
        transfer_key = (
            options["transfer_key"]
            or f"{resource_kind}-{object_id}-{uuid4().hex}"
        )

        if resource_kind == "video":
            video = VideoFile.objects.filter(pk=object_id).first()
            if video is None:
                raise CommandError(f"VideoFile not found: {object_id}")

            payload, media_path, content_type = build_video_transfer_payload(
                video=video,
                transfer_key=transfer_key,
                source_node_key=options["source_node_key"],
                target_node_key=options["target_node_key"],
                source_center_key=options["source_center_key"] or None,
                metadata_only=options["metadata_only"],
            )

        else:
            report = RawPdfFile.objects.filter(pk=object_id).first()
            if report is None:
                raise CommandError(f"RawPdfFile not found: {object_id}")

            payload, media_path, content_type = build_report_transfer_payload(
                report=report,
                transfer_key=transfer_key,
                source_node_key=options["source_node_key"],
                target_node_key=options["target_node_key"],
                source_center_key=options["source_center_key"] or None,
                metadata_only=options["metadata_only"],
            )

        client = HubTransferClient(
            base_url=options["target_url"],
            node_key=options["source_node_key"],
            node_secret=node_secret,
            verify_tls=not options["insecure_skip_tls_verify"],
        )

        self.stdout.write(f"Creating transfer: {transfer_key}")
        created = client.create_transfer(payload)
        self.stdout.write(self.style.SUCCESS(f"Transfer response: {created}"))

        if media_path is not None and content_type is not None:
            self.stdout.write(f"Uploading processed media: {media_path}")
            uploaded = client.upload_processed_media(
                transfer_key=transfer_key,
                file_path=media_path,
                content_type=content_type,
            )
            self.stdout.write(self.style.SUCCESS(f"Media response: {uploaded}"))

        status = client.get_status(transfer_key)
        self.stdout.write(self.style.SUCCESS(f"Final status: {status}"))