from __future__ import annotations

import json
import uuid
from collections import Counter

from django.core.management.base import BaseCommand, CommandError, CommandParser
from pydantic import ValidationError

from endoreg_db.config.env import upload_job_source_reaper_apply_enabled
from endoreg_db.schemas.upload_job_source_reaper import ReapUploadJobSourcesOptions
from endoreg_db.services.hub.cleanup import (
    UploadSourceCleanupItem,
    UploadSourceReaperResult,
    run_upload_job_source_reaper,
)


def _payload(result: UploadSourceReaperResult, *, apply: bool) -> dict[str, object]:
    by_media_type = Counter(item.media_type.value for item in result.items)
    by_ingest_mode = Counter(item.ingest_mode for item in result.items)
    by_decision = Counter(item.decision.value for item in result.items)
    by_blocker = Counter(item.blocker.value for item in result.items)
    return {
        "mode": "apply" if apply else "dry_run",
        "selected": len(result.items),
        "cleaned": result.cleaned,
        "reclaimable_bytes": result.reclaimable_bytes,
        "freed_bytes": result.freed_bytes,
        "inventory": {
            "by_media_type": dict(sorted(by_media_type.items())),
            "by_ingest_mode": dict(sorted(by_ingest_mode.items())),
            "by_decision": dict(sorted(by_decision.items())),
            "by_blocker": dict(sorted(by_blocker.items())),
        },
        "items": [item.as_dict() for item in result.items],
    }


class Command(BaseCommand):
    help = (
        "Inspect persisted UploadJob sources and, with explicit authorization, "
        "delete only sources whose fenced media-integrity contract is satisfied."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--upload-job-id",
            type=uuid.UUID,
            default=None,
            help="Inspect or apply cleanup to exactly one UploadJob UUID.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Positive maximum number of cleanup candidates in one batch.",
        )
        parser.add_argument(
            "--repeat-until-empty",
            action="store_true",
            help="Apply additional explicit batches until no source was cleaned.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply authorized deletions. Without this flag the command is a dry-run.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Write a machine-readable inventory without sensitive paths.",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            command_options = ReapUploadJobSourcesOptions.model_validate(options)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        if command_options.apply and not upload_job_source_reaper_apply_enabled():
            raise CommandError(
                "UploadJob source reaper apply is disabled. Set "
                "UPLOAD_JOB_SOURCE_REAPER_APPLY_ENABLED=true only after reviewing "
                "a bounded dry-run."
            )
        if command_options.repeat_until_empty and not command_options.apply:
            raise CommandError("--repeat-until-empty requires --apply")

        combined_items: list[UploadSourceCleanupItem] = []
        while True:
            result = run_upload_job_source_reaper(
                apply=command_options.apply,
                upload_job_id=command_options.upload_job_id,
                limit=command_options.limit,
            )
            combined_items.extend(result.items)
            if not command_options.repeat_until_empty or result.cleaned == 0:
                break

        combined = UploadSourceReaperResult(items=tuple(combined_items))
        payload = _payload(combined, apply=command_options.apply)
        if command_options.json_output:
            self.stdout.write(json.dumps(payload, sort_keys=True))
            return

        self.stdout.write(
            "mode={mode} selected={selected} cleaned={cleaned} "
            "reclaimable_bytes={reclaimable_bytes} freed_bytes={freed_bytes}".format(
                **payload
            )
        )
        for item in combined.items:
            self.stdout.write(
                "upload_job_id={upload_job_id} decision={decision} blocker={blocker} "
                "media_type={media_type} ingest_mode={ingest_mode} "
                "age_seconds={age_seconds} reclaimable_bytes={reclaimable_bytes} "
                "receipt_id={receipt_id}".format(**item.as_dict())
            )
