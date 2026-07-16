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
from endoreg_db.services.hub.transfer_logging import (
    decision,
    error,
    info,
    json_block,
    kv,
    model_identity,
    path_info,
    section,
    step,
    success,
    transfer_summary,
    warning,
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
        section("SENDER INITIALIZATION", "🚀")
    
        secret_path = Path(options["source_node_secret_file"]).expanduser().resolve()
    
        step(1, "Validate sender configuration")
        kv("Target URL", options["target_url"])
        kv("Source node key", options["source_node_key"])
        kv("Target node key", options["target_node_key"])
        kv("Source center key", options["source_center_key"] or "<automatic>")
        kv("Resource kind", options["resource_kind"])
        kv("Source object ID", options["object_id"])
        kv("Metadata only", options["metadata_only"])
        kv("TLS verification enabled", not options["insecure_skip_tls_verify"])
        path_info(label="Node secret file", path=secret_path)
    
        if not secret_path.is_file():
            error(f"Secret file not found: {secret_path}")
            raise CommandError(f"Secret file not found: {secret_path}")
    
        node_secret = secret_path.read_text(encoding="utf-8").strip()
        if not node_secret:
            error("Node secret file is empty")
            raise CommandError("Node secret file is empty")
    
        success("Node secret file is present and non-empty")
        info("The node secret itself will not be printed")
    
        resource_kind = options["resource_kind"]
        object_id = options["object_id"]
        transfer_key = (
            options["transfer_key"]
            or f"{resource_kind}-{object_id}-{uuid4().hex}"
        )
    
        step(2, "Load source database object")
    
        if resource_kind == "video":
            video = VideoFile.objects.filter(pk=object_id).first()
    
            if video is None:
                error(f"VideoFile not found: {object_id}")
                raise CommandError(f"VideoFile not found: {object_id}")
    
            state = getattr(video, "state", None)
    
            model_identity(
                model_name="Source VideoFile",
                local_id=video.pk,
                portable_hash=str(video.video_hash or ""),
                node_key=options["source_node_key"],
            )
    
            kv("Center ID", video.center_id)
            kv(
                "Center key",
                getattr(getattr(video, "center", None), "center_key", None),
            )
            kv("Original filename", video.original_file_name)
            kv("Processed storage name", getattr(video.processed_file, "name", None))
            kv("Processed video hash", video.processed_video_hash)
            kv(
                "Anonymization status",
                getattr(
                    getattr(state, "anonymization_status", None),
                    "value",
                    "<missing>",
                ),
            )
            kv(
                "Anonymization validated",
                getattr(state, "anonymization_validated", False),
            )
            kv("Processing error", getattr(state, "processing_error", False))
    
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
                error(f"RawPdfFile not found: {object_id}")
                raise CommandError(f"RawPdfFile not found: {object_id}")
    
            model_identity(
                model_name="Source RawPdfFile",
                local_id=report.pk,
                portable_hash=str(getattr(report, "pdf_hash", "") or ""),
                node_key=options["source_node_key"],
            )
    
            payload, media_path, content_type = build_report_transfer_payload(
                report=report,
                transfer_key=transfer_key,
                source_node_key=options["source_node_key"],
                target_node_key=options["target_node_key"],
                source_center_key=options["source_center_key"] or None,
                metadata_only=options["metadata_only"],
            )
    
        step(3, "Inspect generated transfer payload")
    
        transfer_summary(
            transfer_key=transfer_key,
            resource_kind=payload["resource_kind"],
            source_node_key=payload["source_node_key"],
            target_node_key=payload["target_node_key"],
            resource_hash=payload["resource_hash"],
            transfer_mode=payload["transfer_mode"],
        )
    
        json_block("Outgoing transfer payload", payload)
    
        path_info(
            label="Processed media source path",
            path=media_path,
            check_exists=True,
        )
        kv("Upload content type", content_type or "<metadata-only>")
    
        step(4, "Initialize HTTP transfer client")
    
        client = HubTransferClient(
            base_url=options["target_url"],
            node_key=options["source_node_key"],
            node_secret=node_secret,
            verify_tls=not options["insecure_skip_tls_verify"],
        )
    
        success("HTTP transfer client initialized")
        info("Authentication secret is attached to request headers but is not displayed")
    
        step(5, "Create transfer record on receiver")
    
        self.stdout.write(f"Creating transfer: {transfer_key}")
    
        try:
            created = client.create_transfer(payload)
        except Exception as exc:
            error("Receiver rejected transfer creation")
            kv("Exception type", type(exc).__name__)
            kv("Exception", str(exc))
            raise
    
        json_block("Receiver transfer-create response", created)
        success("Receiver accepted transfer metadata")
    
        if media_path is not None and content_type is not None:
            step(6, "Upload processed media")
    
            path_info(
                label="Media file being uploaded",
                path=media_path,
                check_exists=True,
            )
            kv("Media content type", content_type)
    
            try:
                uploaded = client.upload_processed_media(
                    transfer_key=transfer_key,
                    file_path=media_path,
                    content_type=content_type,
                )
            except Exception as exc:
                error("Processed media upload failed")
                kv("Exception type", type(exc).__name__)
                kv("Exception", str(exc))
                raise
    
            json_block("Receiver media-upload response", uploaded)
            success("Processed media uploaded successfully")
        else:
            warning("Metadata-only mode: no MP4/PDF bytes were uploaded")
    
        step(7, "Retrieve final transfer status")
    
        try:
            status = client.get_status(transfer_key)
        except Exception as exc:
            error("Could not retrieve final transfer status")
            kv("Exception type", type(exc).__name__)
            kv("Exception", str(exc))
            raise
    
        json_block("Final receiver transfer status", status)
    
        decision("TRANSFER RESULT")
        kv("Transfer key", transfer_key)
        kv("Receiver status", status.get("transfer_status"))
        kv("Receiver object ID", status.get("target_object_id"))
        kv("Processing decision", status.get("processing_decision"))
        kv("Status detail", status.get("status_detail"))
    
        success("Transfer command completed")