from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.services.hub.quarantine import (
    QuarantineApprovalResult,
    QuarantineReapResult,
    QuarantineSyncResult,
    approve_stale_quarantine_items,
    reap_approved_quarantine_items,
    stale_pending_review_items,
    sync_quarantine_inventory,
)
from lx_dtypes.models.contracts.json_types import JsonObject
from lx_dtypes.models.contracts.management_command import (
    ReapQuarantineCommandOptionsPayload,
)

if TYPE_CHECKING:
    from endoreg_db.models.hub.quarantine_item import QuarantineItem


@dataclass(frozen=True)
class _ReapQuarantineOptions:
    older_than_days: int
    dry_run: bool
    approve_stale: bool
    decision_reason: str
    reviewed_by_username: str
    delete_after_days: int
    json_output: bool


def _parse_delete_after_days(options: dict[str, object]) -> int:
    raw_delete_after_days = options.get("delete_after_days", 0)
    if isinstance(raw_delete_after_days, int):
        delete_after_days = raw_delete_after_days
    else:
        delete_after_days = int(str(raw_delete_after_days or 0))
    if delete_after_days < 0:
        raise CommandError("delete_after_days must not be negative")
    return delete_after_days


def _parse_command_options(options: dict[str, object]) -> _ReapQuarantineOptions:
    try:
        command_options = ReapQuarantineCommandOptionsPayload.model_validate(options)
    except ValidationError as exc:
        raise CommandError(str(exc)) from exc

    return _ReapQuarantineOptions(
        older_than_days=command_options.older_than_days,
        dry_run=command_options.dry_run or not command_options.confirm,
        approve_stale=bool(options.get("approve_stale", False)),
        decision_reason=str(options.get("decision_reason", "") or "").strip(),
        reviewed_by_username=str(options.get("reviewed_by", "") or "").strip(),
        delete_after_days=_parse_delete_after_days(options),
        json_output=command_options.json_output,
    )


def _resolve_reviewer(username: str) -> User | None:
    if not username:
        return None
    reviewer = User.objects.filter(username=username).first()
    if reviewer is None:
        raise CommandError(f"Unknown reviewer username: {username}")
    return reviewer


def _approve_stale_items(
    options: _ReapQuarantineOptions,
    *,
    reviewer: User | None,
) -> QuarantineApprovalResult | None:
    if not options.approve_stale:
        return None
    if not options.decision_reason:
        raise CommandError("--decision-reason is required with --approve-stale")
    return approve_stale_quarantine_items(
        older_than_days=options.older_than_days,
        reason=options.decision_reason,
        reviewed_by=reviewer,
        delete_after_days=options.delete_after_days,
    )


def _build_result_payload(
    options: _ReapQuarantineOptions,
    *,
    sync_result: QuarantineSyncResult,
    approval_result: QuarantineApprovalResult | None,
    reap_result: QuarantineReapResult,
    pending_review: Sequence[QuarantineItem],
) -> JsonObject:
    return {
        "quarantine_dir": str(sync_result.quarantine_dir),
        "older_than_days": options.older_than_days,
        "dry_run": options.dry_run,
        "approve_stale": options.approve_stale,
        "approved_count": (
            approval_result.approved_count if approval_result is not None else 0
        ),
        "delete_after_days": options.delete_after_days,
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


def _write_result(
    command: BaseCommand,
    options: _ReapQuarantineOptions,
    *,
    payload: JsonObject,
    approval_result: QuarantineApprovalResult | None,
    reap_result: QuarantineReapResult,
    pending_review: Sequence[QuarantineItem],
) -> None:
    if options.json_output:
        command.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        return

    mode = "dry-run" if options.dry_run else "confirmed"
    command.stdout.write(
        f"{mode}: {reap_result.candidate_count} approved quarantine files "
        f"eligible for deletion; {len(pending_review)} pending review"
    )
    if approval_result is not None:
        command.stdout.write(f"approved {approval_result.approved_count} files")
    if reap_result.deleted:
        command.stdout.write(f"deleted {reap_result.deleted_count} files")


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
        command_options = _parse_command_options(options)
        reviewer = _resolve_reviewer(command_options.reviewed_by_username)
        sync_result = sync_quarantine_inventory()
        approval_result = _approve_stale_items(
            command_options,
            reviewer=reviewer,
        )
        reap_result = reap_approved_quarantine_items(
            older_than_days=command_options.older_than_days,
            dry_run=command_options.dry_run,
        )
        pending_review = stale_pending_review_items(
            older_than_days=command_options.older_than_days
        )
        payload = _build_result_payload(
            command_options,
            sync_result=sync_result,
            approval_result=approval_result,
            reap_result=reap_result,
            pending_review=pending_review,
        )
        _write_result(
            self,
            command_options,
            payload=payload,
            approval_result=approval_result,
            reap_result=reap_result,
            pending_review=pending_review,
        )
