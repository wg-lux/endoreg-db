from __future__ import annotations

import json

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.services.hub.quarantine import (
    approve_stale_quarantine_items,
    reap_approved_quarantine_items,
    stale_pending_review_items,
    sync_quarantine_inventory,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.management_command import (
    ReapQuarantineCommandOptionsPayload,
)


class Command(BaseCommand):
    help = "Report or delete stale files from the local quarantine directory."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=30,
            help="Select quarantine files older than this many days.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report candidates without deleting them.",
        )
        parser.add_argument(
            "--confirm",
            action="store_true",
            default=False,
            help="Delete matching files. Without this flag the command is dry-run.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the result as JSON.",
        )
        parser.add_argument(
            "--approve-stale",
            action="store_true",
            default=False,
            help="Approve stale pending-review quarantine files for deletion.",
        )
        parser.add_argument(
            "--decision-reason",
            default="",
            help="Required reason when approving stale quarantine files.",
        )
        parser.add_argument(
            "--reviewed-by",
            default="",
            help="Optional Django username to attach to the approval decision.",
        )
        parser.add_argument(
            "--delete-after-days",
            type=int,
            default=0,
            help="Delay deletion eligibility this many days after approval.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            command_options = ReapQuarantineCommandOptionsPayload.model_validate(
                options
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        older_than_days = command_options.older_than_days
        dry_run = command_options.dry_run or not command_options.confirm
        approve_stale = bool(options.get("approve_stale", False))
        decision_reason = str(options.get("decision_reason", "") or "").strip()
        reviewed_by_username = str(options.get("reviewed_by", "") or "").strip()
        raw_delete_after_days = options.get("delete_after_days", 0)
        if isinstance(raw_delete_after_days, int):
            delete_after_days = raw_delete_after_days
        else:
            delete_after_days = int(str(raw_delete_after_days or 0))
        if delete_after_days < 0:
            raise CommandError("delete_after_days must not be negative")

        reviewer: User | None = None
        if reviewed_by_username:
            reviewer = User.objects.filter(username=reviewed_by_username).first()
            if reviewer is None:
                raise CommandError(f"Unknown reviewer username: {reviewed_by_username}")

        sync_result = sync_quarantine_inventory()
        approval_result = None
        if approve_stale:
            if not decision_reason:
                raise CommandError("--decision-reason is required with --approve-stale")
            approval_result = approve_stale_quarantine_items(
                older_than_days=older_than_days,
                reason=decision_reason,
                reviewed_by=reviewer,
                delete_after_days=delete_after_days,
            )

        reap_result = reap_approved_quarantine_items(
            older_than_days=older_than_days,
            dry_run=dry_run,
        )
        pending_review = stale_pending_review_items(older_than_days=older_than_days)

        payload: JsonObject = {
            "quarantine_dir": str(sync_result.quarantine_dir),
            "older_than_days": older_than_days,
            "dry_run": dry_run,
            "approve_stale": approve_stale,
            "approved_count": (
                approval_result.approved_count if approval_result is not None else 0
            ),
            "delete_after_days": delete_after_days,
            "sync": {
                "scanned_count": sync_result.scanned_count,
                "created_count": sync_result.created_count,
                "updated_count": sync_result.updated_count,
                "missing_count": sync_result.missing_count,
                "total_bytes": sync_result.total_bytes,
            },
            "pending_review_count": len(pending_review),
            "pending_review": [item.path for item in pending_review],
            "candidate_count": reap_result.candidate_count,
            "candidate_bytes": reap_result.candidate_bytes,
            "deleted_count": reap_result.deleted_count,
            "missing_count": reap_result.missing_count,
            "candidates": [item.path for item in reap_result.candidates],
            "deleted": [item.path for item in reap_result.deleted],
        }

        if command_options.json_output:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        mode = "dry-run" if dry_run else "confirmed"
        self.stdout.write(
            f"{mode}: {reap_result.candidate_count} approved quarantine files "
            f"eligible for deletion; {len(pending_review)} pending review"
        )
        if approval_result is not None:
            self.stdout.write(f"approved {approval_result.approved_count} files")
        if reap_result.deleted:
            self.stdout.write(f"deleted {reap_result.deleted_count} files")
